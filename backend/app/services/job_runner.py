from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite

from ..observability import ensure_trace_id
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
        self._tasks: dict[int, asyncio.Task] = {}
        self._handlers: dict[str, JobHandler] = {}
        self._running = False

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

        job = await job_repo.create_job(
            db,
            job_type=job_type,
            book_id=book_id,
            chapter_idx=chapter_idx,
            window_id=window_id,
        )

        await publish_event(
            "window.queued",
            {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "window_id": window_id,
                "job_id": job["id"],
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

        await publish_event(
            "window.queued",
            {
                "book_id": job["book_id"],
                "chapter_idx": job["chapter_idx"],
                "window_id": job.get("window_id"),
                "job_id": job_id,
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
            self._execute_with_semaphore(db, job)
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda t, jid=job_id: self._tasks.pop(jid, None))

    async def _execute_with_semaphore(
        self, db: aiosqlite.Connection, job: dict[str, Any]
    ) -> None:
        async with self._semaphore:
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

        trace_id = ensure_trace_id()

        await job_repo.update_job_status(
            db, job_id, "running", trace_id=trace_id
        )

        if window_id:
            await window_repo.update_window_status(db, window_id, "running")

        await publish_event(
            "window.running",
            {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "window_id": window_id,
                "job_id": job_id,
                "trace_id": trace_id,
            },
        )

        try:
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

            await publish_event(
                "window.done",
                {
                    "book_id": book_id,
                    "chapter_idx": chapter_idx,
                    "window_id": window_id,
                    "job_id": job_id,
                    "trace_id": trace_id,
                },
            )

            logger.info(
                "job_runner.job_done",
                extra={
                    "event": "job_runner.job_done",
                    "fields": {"job_id": job_id, "job_type": job_type},
                },
            )

        except Exception as exc:
            error_msg = str(exc)[:500]

            await job_repo.update_job_status(
                db, job_id, "failed", error=error_msg
            )

            if window_id:
                await window_repo.update_window_status(
                    db, window_id, "failed", error=error_msg
                )

            await publish_event(
                "window.failed",
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
