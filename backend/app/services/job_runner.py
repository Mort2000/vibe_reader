from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from ..application.agent_run_recorder import AgentRunRecorder
from ..application.job_handlers import JobHandler
from ..domain.models import ReadingWindow
from ..application.pending_progress import PendingProgressProcessor
from ..infrastructure.events import EventPublisher, SSEEventPublisher
from ..infrastructure.settings import SettingsProvider
from ..observability import new_trace_id
from ..repos import context_state
from ..repos import jobs as job_repo
from ..repos import windows as window_repo

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(
        self,
        settings_provider: SettingsProvider,
        max_concurrent: int = 2,
        token_estimator: Any = None,
        event_publisher: EventPublisher | None = None,
        recorder: AgentRunRecorder | None = None,
        pending_processor: PendingProgressProcessor | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._book_locks: dict[int, asyncio.Lock] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._handlers: dict[str, JobHandler] = {}
        self._running = False
        self._token_estimator = token_estimator
        self._event_publisher: EventPublisher = event_publisher or SSEEventPublisher()
        self._settings_provider = settings_provider
        self._recorder = recorder
        self._pending_processor = pending_processor

    def _get_book_lock(self, book_id: int) -> asyncio.Lock:
        lock = self._book_locks.get(book_id)
        if lock is None:
            lock = asyncio.Lock()
            self._book_locks[book_id] = lock
        return lock

    def register_handler(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    @asynccontextmanager
    async def book_lock(self, book_id: int):
        lock = self._get_book_lock(book_id)
        async with lock:
            yield

    @property
    def recorder(self) -> AgentRunRecorder | None:
        return self._recorder

    async def start(self) -> None:
        self._running = True
        logger.info(
            "job_runner.started",
            extra={"event": "job_runner.started"},
        )

    async def stop(self) -> None:
        self._running = False
        for job_id, task in list(self._tasks.items()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        logger.info(
            "job_runner.stopped",
            extra={"event": "job_runner.stopped"},
        )

    async def recover_jobs(self, db: aiosqlite.Connection) -> None:
        running_jobs, _ = await job_repo.list_jobs(db, status="running", limit=100)
        for job in running_jobs:
            await job_repo.update_job_status(db, job["id"], "pending")
            await self._enqueue_job(db, job)
            logger.info(
                "job_runner.recovered",
                extra={
                    "event": "job_runner.recovered",
                    "fields": {"job_id": job["id"], "from": "running"},
                },
            )

        pending_jobs, _ = await job_repo.list_jobs(db, status="pending", limit=100)
        for job in pending_jobs:
            if job["id"] not in self._tasks:
                await self._enqueue_job(db, job)
                logger.info(
                    "job_runner.recovered",
                    extra={
                        "event": "job_runner.recovered",
                        "fields": {"job_id": job["id"], "from": "pending"},
                    },
                )

    async def submit_job(
        self,
        db: aiosqlite.Connection,
        job_type: str,
        book_id: int,
        chapter_idx: int,
        window_id: int | None = None,
    ) -> dict[str, Any]:
        existing, _ = await job_repo.list_jobs(
            db,
            book_id=book_id,
            chapter_idx=chapter_idx,
            job_type=job_type,
            status="pending",
            limit=10,
        )
        for j in existing:
            if j.get("window_id") == window_id:
                return j
            if window_id is None and j.get("window_id") is None:
                return j

        existing_running, _ = await job_repo.list_jobs(
            db,
            book_id=book_id,
            chapter_idx=chapter_idx,
            job_type=job_type,
            status="running",
            limit=10,
        )
        for j in existing_running:
            if j.get("window_id") == window_id:
                return j
            if window_id is None and j.get("window_id") is None:
                return j

        job = await job_repo.create_job(
            db,
            job_type=job_type,
            book_id=book_id,
            chapter_idx=chapter_idx,
            window_id=window_id,
        )

        event_type = (
            "window.queued" if job_type == "comment_window" else "context.queued"
        )
        await self._event_publisher.publish(
            event_type,
            {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "window_id": window_id,
                "job_id": job["id"],
                "job_type": job_type,
            },
        )

        await self._enqueue_job(db, job)
        return job

    async def retry_job(
        self,
        db: aiosqlite.Connection,
        job_id: int,
    ) -> dict[str, Any]:
        job = await job_repo.get_job(db, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        await job_repo.increment_attempt(db, job_id)
        await job_repo.update_job_status(db, job_id, "pending", error=None)

        job["status"] = "pending"
        job["attempt_count"] = job.get("attempt_count", 0) + 1

        retry_event = (
            "window.queued"
            if job.get("job_type") == "comment_window"
            else "context.queued"
        )
        await self._event_publisher.publish(
            retry_event,
            {
                "book_id": job["book_id"],
                "chapter_idx": job["chapter_idx"],
                "window_id": job.get("window_id"),
                "job_id": job_id,
                "job_type": job.get("job_type"),
            },
        )

        await self._enqueue_job(db, job)
        return job

    async def _enqueue_job(self, db: aiosqlite.Connection, job: dict[str, Any]) -> None:
        if not self._running:
            return

        job_id = job["id"]
        task = asyncio.create_task(self._execute_with_book_lock(db, job))
        self._tasks[job_id] = task
        task.add_done_callback(lambda t, jid=job_id: self._tasks.pop(jid, None))

    async def _execute_with_book_lock(
        self, db: aiosqlite.Connection, job: dict[str, Any]
    ) -> None:
        async with self._semaphore:
            book_lock = self._get_book_lock(job["book_id"])
            async with book_lock:
                try:
                    await self._execute_job(db, job)
                except Exception:
                    logger.exception(
                        "job_runner.task_failed",
                        extra={
                            "event": "job_runner.task_failed",
                            "fields": {"job_id": job["id"]},
                        },
                    )

    async def _execute_job(self, db: aiosqlite.Connection, job: dict[str, Any]) -> None:
        job_id = job["id"]
        job_type = job["job_type"]
        window_id = job.get("window_id")
        book_id = job["book_id"]
        chapter_idx = job["chapter_idx"]

        handler = self._handlers.get(job_type)
        if handler is None:
            logger.error(
                "job_runner.no_handler",
                extra={
                    "event": "job_runner.no_handler",
                    "fields": {"job_id": job_id, "job_type": job_type},
                },
            )
            return

        trace_id = new_trace_id()

        await context_state.get_or_create(db, book_id)
        await context_state.update_state(
            db, book_id, status="running", running_job_id=job_id
        )

        await job_repo.update_job_status(db, job_id, "running", trace_id=trace_id)

        if window_id:
            await window_repo.update_window_status(db, window_id, "running")

        running_event = (
            "window.running" if job_type == "comment_window" else "context.compacting"
        )
        await self._event_publisher.publish(
            running_event,
            {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "window_id": window_id,
                "job_id": job_id,
                "job_type": job_type,
                "trace_id": trace_id,
            },
        )

        try:
            await self._run_handler(
                db,
                job_id,
                job_type,
                window_id,
                book_id,
                chapter_idx,
                trace_id,
                handler,
            )
        except Exception as exc:
            await self._handle_failure(
                db,
                job_id,
                job_type,
                window_id,
                book_id,
                chapter_idx,
                trace_id,
                exc,
            )

    async def _skip_compaction_job(
        self,
        db: aiosqlite.Connection,
        job_id: int,
        book_id: int,
        chapter_idx: int,
    ) -> None:
        await job_repo.update_job_status(db, job_id, "skipped")
        logger.info(
            "job_runner.compaction_skipped",
            extra={
                "event": "job_runner.compaction_skipped",
                "fields": {
                    "job_id": job_id,
                    "book_id": book_id,
                    "chapter_idx": chapter_idx,
                },
            },
        )
        await self._finalize_job(db, book_id)

    async def _run_handler(
        self,
        db: aiosqlite.Connection,
        job_id: int,
        job_type: str,
        window_id: int | None,
        book_id: int,
        chapter_idx: int,
        trace_id: str,
        handler: JobHandler,
    ) -> None:
        window: ReadingWindow | None = None
        if window_id:
            window = await window_repo.get_window(db, window_id)

        settings = self._settings_provider.current()

        result = await handler.run(
            db, job_id, window, settings, self._token_estimator
        )

        if result is None:
            await self._skip_compaction_job(db, job_id, book_id, chapter_idx)
            return

        telemetry = result.telemetry

        if telemetry and self._recorder is not None:
            await self._recorder.record(
                db,
                result=telemetry,
                settings=settings,
                trace_id=trace_id,
                job_id=job_id,
                book_id=book_id,
                chapter_idx=chapter_idx,
                window_id=window_id,
            )

        await job_repo.update_job_status(db, job_id, "done")

        if window_id:
            await window_repo.update_window_status(db, window_id, "done")

        if telemetry or job_type == "comment_window":
            done_event = (
                "window.done" if job_type == "comment_window" else "context.compacted"
            )
            event_payload: dict[str, Any] = {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "window_id": window_id,
                "job_id": job_id,
                "job_type": job_type,
                "trace_id": trace_id,
            }
            event_payload.update(result.done_event_extras)
            await self._event_publisher.publish(
                done_event,
                event_payload,
            )

        if job_type == "comment_window" and telemetry is not None:
            audit_context = getattr(telemetry, "audit_context", None)
            for comment in getattr(audit_context, "valid_comments", []) or []:
                await self._event_publisher.publish(
                    "comment.created",
                    {
                        "book_id": book_id,
                        "chapter_idx": chapter_idx,
                        "paragraph_idx": comment.get("paragraph_idx"),
                        "window_id": window_id,
                        "job_id": job_id,
                        "comment_id": comment.get("comment_id"),
                        "trace_id": comment.get("trace_id") or trace_id,
                    },
                )

        logger.info(
            "job_runner.job_done",
            extra={
                "event": "job_runner.job_done",
                "fields": {"job_id": job_id, "job_type": job_type},
            },
        )

        await self._finalize_job(db, book_id)

    async def _finalize_job(
        self,
        db: aiosqlite.Connection,
        book_id: int,
    ) -> None:
        state = await context_state.get_or_create(db, book_id)
        has_pending = state.pending_chapter_idx is not None

        if has_pending and self._pending_processor is not None:
            settings = self._settings_provider.current()
            await self._pending_processor.process(
                db, book_id, state, settings, self
            )
        else:
            await context_state.update_state(
                db, book_id, status="idle", running_job_id=None
            )

    async def _handle_failure(
        self,
        db: aiosqlite.Connection,
        job_id: int,
        job_type: str,
        window_id: int | None,
        book_id: int,
        chapter_idx: int,
        trace_id: str,
        exc: Exception,
    ) -> None:
        error_msg = str(exc)[:500]

        await job_repo.update_job_status(db, job_id, "failed", error=error_msg)

        if window_id:
            await window_repo.update_window_status(
                db, window_id, "failed", error=error_msg
            )

        failed_event = (
            "window.failed" if job_type == "comment_window" else "context.failed"
        )
        await self._event_publisher.publish(
            failed_event,
            {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "window_id": window_id,
                "job_id": job_id,
                "error": error_msg,
                "trace_id": trace_id,
            },
        )

        await self._event_publisher.publish(
            "job.failed",
            {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "window_id": window_id,
                "job_id": job_id,
                "job_type": job_type,
                "error": error_msg,
                "trace_id": trace_id,
            },
        )

        await context_state.update_state(
            db,
            book_id,
            status="idle",
            running_job_id=None,
            last_error=error_msg,
        )

        logger.error(
            "job_runner.job_failed",
            extra={
                "event": "job_runner.job_failed",
                "fields": {
                    "job_id": job_id,
                    "job_type": job_type,
                    "error": error_msg,
                },
            },
        )
