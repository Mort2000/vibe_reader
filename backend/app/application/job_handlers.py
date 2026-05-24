from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import aiosqlite

from ..domain.models import ReadingWindow
from .agent_run_result import AgentRunResult


@dataclass
class JobRunResult:
    telemetry: AgentRunResult | None
    done_event_extras: dict[str, Any] = field(default_factory=dict)
    preflight_triggered: bool = False


class JobSubmitter(Protocol):
    async def submit_job(
        self,
        db: aiosqlite.Connection,
        job_type: str,
        book_id: int,
        chapter_idx: int,
        window_id: int | None = None,
    ) -> dict[str, Any]: ...


class JobHandler(Protocol):
    async def run(
        self,
        db: aiosqlite.Connection,
        job_id: int,
        window: ReadingWindow | None,
        settings: Any,
        token_estimator: Any,
    ) -> JobRunResult | None: ...


class CommentJobHandler:
    def __init__(self, job_submitter: JobSubmitter) -> None:
        self._job_submitter = job_submitter

    async def run(
        self,
        db: aiosqlite.Connection,
        job_id: int,
        window: ReadingWindow | None,
        settings: Any,
        token_estimator: Any,
    ) -> JobRunResult | None:
        from ..services.comment_service import run_comment_task

        result = await run_comment_task(
            db, job_id, window, settings, token_estimator
        )

        if result.preflight_triggered and window is not None:
            from ..services.compaction_service import maybe_enqueue_compaction

            await maybe_enqueue_compaction(
                db,
                self._job_submitter,
                window.book_id,
                window.chapter_idx,
                settings,
                preflight_triggered=True,
            )

        return JobRunResult(
            telemetry=result,
            preflight_triggered=result.preflight_triggered,
        )


class CompactionJobHandler:
    async def run(
        self,
        db: aiosqlite.Connection,
        job_id: int,
        window: ReadingWindow | None,
        settings: Any,
        token_estimator: Any,
    ) -> JobRunResult | None:
        from ..services.compaction_service import run_compaction_task

        result = await run_compaction_task(
            db, job_id, window, settings, token_estimator
        )
        if result is None:
            return None

        extras: dict[str, Any] = {}
        for key in (
            "reclaimed_chunk_id",
            "reclaimed_chunk_ids",
            "source_chunk_id",
            "summary_id",
        ):
            val = getattr(result, key, None)
            if val is not None:
                extras[key] = val
        return JobRunResult(telemetry=result, done_event_extras=extras)
