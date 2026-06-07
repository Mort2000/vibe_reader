from __future__ import annotations

import pytest

from app.application.agent_run_recorder import AgentRunRecorder
from app.application.agent_run_result import AgentRunResult
from app.config import ModelConfig, ModelDefaultsConfig, Settings


class FakeEstimator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record_observation(self, db, **kwargs) -> None:  # noqa: ANN001
        self.calls.append(kwargs)


class FakeAuditSink:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def persist_agent_run(self, db, **kwargs) -> None:  # noqa: ANN001
        self.calls.append(kwargs)


def _manifest() -> dict:
    return {
        "total_estimate": 1000,
        "components": [{"name": "live_original_chunks", "tokens": 500}],
    }


@pytest.mark.asyncio
async def test_recorder_skips_tool_call_token_calibration() -> None:
    estimator = FakeEstimator()
    recorder = AgentRunRecorder(token_estimator=estimator)

    await recorder._record_token_calibration(
        object(),
        AgentRunResult(
            agent_name="ParagraphCommentAgent",
            duration_ms=1.0,
            input_tokens=2200,
            tool_call_count=1,
            usage_scope="run_aggregate",
            context_estimated_tokens=1000,
            prompt_version="comment_v1",
            prompt_manifest=_manifest(),
        ),
        Settings(),
    )

    assert estimator.calls == []


@pytest.mark.asyncio
async def test_recorder_skips_aggregate_usage_without_successful_tool_count() -> None:
    estimator = FakeEstimator()
    recorder = AgentRunRecorder(token_estimator=estimator)

    await recorder._record_token_calibration(
        object(),
        AgentRunResult(
            agent_name="ParagraphCommentAgent",
            duration_ms=1.0,
            input_tokens=2200,
            tool_call_count=0,
            usage_scope="run_aggregate",
            context_estimated_tokens=1000,
            prompt_version="comment_v1",
            prompt_manifest=_manifest(),
        ),
        Settings(),
    )

    assert estimator.calls == []


@pytest.mark.asyncio
async def test_recorder_records_non_tool_token_calibration() -> None:
    estimator = FakeEstimator()
    recorder = AgentRunRecorder(token_estimator=estimator)

    await recorder._record_token_calibration(
        object(),
        AgentRunResult(
            agent_name="ReadingChatAgent",
            duration_ms=1.0,
            input_tokens=1100,
            context_estimated_tokens=1000,
            prompt_version="chat_v1",
            prompt_manifest=_manifest(),
        ),
        Settings(),
    )

    assert len(estimator.calls) == 1
    assert estimator.calls[0]["raw_estimate"] == 1000
    assert estimator.calls[0]["actual_tokens"] == 1100


@pytest.mark.asyncio
async def test_recorder_uses_effective_agent_model_identity_for_calibration() -> None:
    estimator = FakeEstimator()
    recorder = AgentRunRecorder(token_estimator=estimator)
    settings = Settings(
        models=[
            ModelConfig(id="chat", model_name="chat-model"),
            ModelConfig(id="comment", model_name="comment-model"),
        ],
        defaults=ModelDefaultsConfig(
            global_model_id="chat",
            chat_model_id="chat",
            comment_model_id="comment",
        ),
    )

    await recorder._record_token_calibration(
        object(),
        AgentRunResult(
            agent_name="ContextCompactionAgent",
            duration_ms=1.0,
            input_tokens=1300,
            context_estimated_tokens=1000,
            prompt_version="compaction_v1",
            prompt_manifest=_manifest(),
        ),
        settings,
    )

    assert estimator.calls[0]["model"] == "openai_compatible:comment:comment-model"


@pytest.mark.asyncio
async def test_recorder_uses_observability_audit_enabled_without_verify_mode() -> None:
    audit_sink = FakeAuditSink()
    recorder = AgentRunRecorder(token_estimator=FakeEstimator(), audit_sink=audit_sink)
    settings = Settings()
    settings.verify_mode = False
    settings.observability.audit.enabled = True

    await recorder.record(
        object(),
        result=AgentRunResult(agent_name="ReadingChatAgent", duration_ms=1.0),
        settings=settings,
        trace_id="trace_x",
        job_id=1,
        book_id=2,
        chapter_idx=3,
        window_id=None,
    )

    assert len(audit_sink.calls) == 1
    assert audit_sink.calls[0]["trace_id"] == "trace_x"
