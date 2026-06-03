from __future__ import annotations

import logging
from typing import Any

import aiosqlite

from ..infrastructure.audit import AuditSink
from ..services.token_estimator import TokenEstimator
from .agent_run_result import AgentRunResult

logger = logging.getLogger(__name__)


class AgentRunRecorder:
    def __init__(
        self,
        token_estimator: Any = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._token_estimator = token_estimator
        self._audit_sink = audit_sink

    async def record(
        self,
        db: aiosqlite.Connection,
        *,
        result: AgentRunResult,
        settings: Any,
        trace_id: str,
        job_id: int,
        book_id: int,
        chapter_idx: int,
        window_id: int | None,
    ) -> None:
        await self._record_token_calibration(db, result, settings)

        if settings.verify_mode and self._audit_sink is not None:
            await self._audit_sink.persist_agent_run(
                db,
                result=result,
                settings=settings,
                trace_id=trace_id,
                job_id=job_id,
                book_id=book_id,
                chapter_idx=chapter_idx,
                window_id=window_id,
            )

    async def _record_token_calibration(
        self,
        db: aiosqlite.Connection,
        result: AgentRunResult,
        settings: Any,
    ) -> None:
        if result.input_tokens is None:
            return
        if result.usage_scope != "single_request":
            logger.debug(
                "agent_run_recorder.skip_aggregate_token_calibration",
                extra={
                    "event": "agent_run_recorder.skip_aggregate_token_calibration",
                    "fields": {
                        "agent_name": result.agent_name,
                        "usage_scope": result.usage_scope,
                    },
                },
            )
            return
        prompt_manifest = result.prompt_manifest or {}
        has_live_chunks = any(
            c.get("name") == "live_original_chunks"
            for c in prompt_manifest.get("components", [])
        )
        if not has_live_chunks:
            return
        estimator = self._token_estimator
        if estimator is None:
            estimator = TokenEstimator(settings.token_estimation)
        await estimator.record_observation(
            db,
            model=settings.llm.model,
            prompt_version=result.prompt_version,
            language_profile="cjk_mixed",
            raw_estimate=result.context_estimated_tokens
            or prompt_manifest.get("total_estimate", 0),
            actual_tokens=result.input_tokens,
        )
