from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite

from ..observability import new_trace_id
from ..repos import context_state
from ..repos import jobs as job_repo
from ..repos import windows as window_repo
from ..routers.events import publish_event

logger = logging.getLogger(__name__)

JobHandler = Callable[
    [aiosqlite.Connection, int, dict[str, Any], Any],
    Awaitable[dict[str, Any] | None],
]


class JobRunner:
    def __init__(self, max_concurrent: int = 2) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._book_locks: dict[int, asyncio.Lock] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._handlers: dict[str, JobHandler] = {}
        self._running = False

    def _get_book_lock(self, book_id: int) -> asyncio.Lock:
        lock = self._book_locks.get(book_id)
        if lock is None:
            lock = asyncio.Lock()
            self._book_locks[book_id] = lock
        return lock

    def register_handler(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

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
            await asyncio.gather(
                *self._tasks.values(), return_exceptions=True
            )
        self._tasks.clear()
        logger.info(
            "job_runner.stopped",
            extra={"event": "job_runner.stopped"},
        )

    async def recover_jobs(self, db: aiosqlite.Connection) -> None:
        running_jobs, _ = await job_repo.list_jobs(
            db, status="running", limit=100
        )
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

        pending_jobs, _ = await job_repo.list_jobs(
            db, status="pending", limit=100
        )
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
            "window.queued"
            if job_type == "comment_window"
            else "context.queued"
        )
        await publish_event(
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
        await job_repo.update_job_status(
            db, job_id, "pending", error=None
        )

        job["status"] = "pending"
        job["attempt_count"] = job.get("attempt_count", 0) + 1

        retry_event = (
            "window.queued"
            if job.get("job_type") == "comment_window"
            else "context.queued"
        )
        await publish_event(
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

    async def _enqueue_job(
        self, db: aiosqlite.Connection, job: dict[str, Any]
    ) -> None:
        if not self._running:
            return

        job_id = job["id"]
        task = asyncio.create_task(
            self._execute_with_book_lock(db, job)
        )
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

    async def _execute_job(
        self, db: aiosqlite.Connection, job: dict[str, Any]
    ) -> None:
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

        await job_repo.update_job_status(
            db, job_id, "running", trace_id=trace_id
        )

        if window_id:
            await window_repo.update_window_status(db, window_id, "running")

        running_event = (
            "window.running"
            if job_type == "comment_window"
            else "context.compacting"
        )
        await publish_event(
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
                db, job_id, job_type, window_id,
                book_id, chapter_idx, trace_id,
                handler,
            )
        except Exception as exc:
            await self._handle_failure(
                db, job_id, job_type, window_id,
                book_id, chapter_idx, trace_id, exc,
            )

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
        window: dict[str, Any] | None = None
        if window_id:
            window = await window_repo.get_window(db, window_id)

        from ..config import load_settings

        settings = load_settings()

        telemetry = await handler(db, job_id, window, settings)

        if telemetry and settings.verify_mode:
            from ..services.verify_telemetry import persist_agent_run

            await persist_agent_run(
                db,
                trace_id=trace_id,
                job_id=job_id,
                book_id=book_id,
                chapter_idx=chapter_idx,
                window_id=window_id,
                payload=telemetry,
            )

        await job_repo.update_job_status(db, job_id, "done")

        if window_id:
            await window_repo.update_window_status(
                db, window_id, "done"
            )

        if (
            telemetry
            and telemetry.get("preflight_triggered")
            and job_type == "comment_window"
        ):
            await self.submit_job(
                db, "compact_context", book_id, chapter_idx
            )

        if telemetry or job_type == "comment_window":
            done_event = (
                "window.done"
                if job_type == "comment_window"
                else "context.compacted"
            )
            event_payload: dict[str, Any] = {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "window_id": window_id,
                "job_id": job_id,
                "job_type": job_type,
                "trace_id": trace_id,
            }
            if telemetry and job_type == "compact_context":
                for key in (
                    "reclaimed_chunk_id",
                    "reclaimed_chunk_ids",
                    "source_chunk_id",
                    "summary_id",
                ):
                    if telemetry.get(key) is not None:
                        event_payload[key] = telemetry[key]
            await publish_event(
                done_event,
                event_payload,
            )

        logger.info(
            "job_runner.job_done",
            extra={
                "event": "job_runner.job_done",
                "fields": {"job_id": job_id, "job_type": job_type},
            },
        )

        await self._finalize_job(db, book_id, telemetry, settings)

    async def _finalize_job(
        self,
        db: aiosqlite.Connection,
        book_id: int,
        telemetry: dict[str, Any] | None,
        settings: Any,
    ) -> None:
        state = await context_state.get_or_create(db, book_id)
        has_pending = state.get("pending_chapter_idx") is not None

        if has_pending:
            await self._process_pending_progress(db, book_id, state, settings)
        else:
            await context_state.update_state(
                db, book_id, status="idle", running_job_id=None
            )

    async def _process_pending_progress(
        self,
        db: aiosqlite.Connection,
        book_id: int,
        state: dict[str, Any],
        settings: Any,
    ) -> None:
        from ..repos import progress as progress_repo
        from . import window_service
        from .context_builder import build_context
        from .progress_helpers import validate_pending_progress

        chapter_idx = state["pending_chapter_idx"]
        paragraph_idx = state["pending_paragraph_idx"]
        scroll_pct = state["pending_scroll_pct"]
        pending_af_ch = state.get("pending_assistant_frontier_chapter_idx")
        pending_af_p = state.get("pending_assistant_frontier_paragraph_idx")
        pending_jump_chars = state.get("pending_context_jump_chars")

        _clear_pending = {
            "status": "idle",
            "running_job_id": None,
            "pending_chapter_idx": None,
            "pending_paragraph_idx": None,
            "pending_scroll_pct": None,
            "pending_assistant_frontier_chapter_idx": None,
            "pending_assistant_frontier_paragraph_idx": None,
            "pending_context_jump_chars": None,
            "pending_updated_at": None,
        }

        if chapter_idx is None or paragraph_idx is None:
            await context_state.update_state(db, book_id, **_clear_pending)
            return

        jump_type, jump_chars = await validate_pending_progress(
            db,
            book_id,
            state,
            chapter_idx=chapter_idx,
            paragraph_idx=paragraph_idx,
            assistant_frontier_chapter_idx=pending_af_ch,
            assistant_frontier_paragraph_idx=pending_af_p,
            max_context_jump_chars=settings.context.max_context_jump_chars,
            lookahead_paragraphs=settings.reader.lookahead_paragraphs,
        )

        if jump_type in ("backward", "forward_rejected"):
            logger.info(
                "job_runner.pending_discarded",
                extra={
                    "event": "job_runner.pending_discarded",
                    "fields": {
                        "book_id": book_id,
                        "chapter_idx": chapter_idx,
                        "paragraph_idx": paragraph_idx,
                        "jump_type": jump_type,
                        "jump_chars": jump_chars,
                        "stored_jump_chars": pending_jump_chars,
                    },
                },
            )
            await context_state.update_state(db, book_id, **_clear_pending)
            return

        assistant_frontier = pending_af_p
        if assistant_frontier is None:
            from .progress_helpers import compute_assistant_frontier

            assistant_frontier = await compute_assistant_frontier(
                db, book_id, chapter_idx, paragraph_idx,
                settings.reader.lookahead_paragraphs,
            )

        await progress_repo.upsert_progress(
            db, book_id,
            chapter_idx=chapter_idx,
            paragraph_idx=paragraph_idx,
            scroll_pct=scroll_pct or 0.0,
        )

        await context_state.update_state(
            db, book_id,
            active_chapter_idx=chapter_idx,
            reading_paragraph_idx=paragraph_idx,
        )

        window, is_new = await window_service.get_or_create_window(
            db, book_id, chapter_idx, paragraph_idx, settings
        )

        if is_new:
            ctx_result = await build_context(
                db,
                book_id=book_id,
                chapter_idx=chapter_idx,
                reading_pidx=paragraph_idx,
                settings=settings,
            )

            if ctx_result.preflight_triggered:
                await self.submit_job(
                    db, "compact_context", book_id, chapter_idx
                )

            await self.submit_job(
                db, "comment_window", book_id, chapter_idx,
                window_id=window["id"],
            )

        await context_state.update_state(
            db, book_id,
            assistant_frontier_chapter_idx=chapter_idx,
            assistant_frontier_paragraph_idx=assistant_frontier,
            context_frontier_chapter_idx=chapter_idx,
            context_frontier_paragraph_idx=assistant_frontier,
            **_clear_pending,
        )

        logger.info(
            "job_runner.pending_processed",
            extra={
                "event": "job_runner.pending_processed",
                "fields": {
                    "book_id": book_id,
                    "chapter_idx": chapter_idx,
                    "paragraph_idx": paragraph_idx,
                    "assistant_frontier": assistant_frontier,
                    "jump_type": jump_type,
                    "jump_chars": jump_chars,
                },
            },
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

        await job_repo.update_job_status(
            db, job_id, "failed", error=error_msg
        )

        if window_id:
            await window_repo.update_window_status(
                db, window_id, "failed", error=error_msg
            )

        failed_event = (
            "window.failed"
            if job_type == "comment_window"
            else "context.failed"
        )
        await publish_event(
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

        await publish_event(
            "job.failed",
            {
                "job_id": job_id,
                "job_type": job_type,
                "error": error_msg,
                "trace_id": trace_id,
            },
        )

        await context_state.update_state(
            db, book_id, status="idle", running_job_id=None,
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
