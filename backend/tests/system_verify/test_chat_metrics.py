"""Unit tests for S5/S6 chat metric aggregation."""

from __future__ import annotations

import pytest

from tests.system_verify.core.config_loader import load_verify_config
from tests.system_verify.core.client_factory import ChatStreamResult
from tests.system_verify.core.context import ScenarioContext
from tests.system_verify.core.run_manager import RunManager
from tests.system_verify.flows.chat import ChatTurnRecord
from tests.system_verify.flows.metrics import record_s5_chat_metrics
from tests.system_verify.metrics_collector import MetricsAggregator


@pytest.mark.asyncio
async def test_record_s5_chat_metrics_aggregates_percentiles() -> None:
    config = load_verify_config()
    run_manager = RunManager(config, run_id="20260530T120000Z_metrics")
    run_manager.start()
    metrics = MetricsAggregator(run_manager, config)
    ctx = ScenarioContext(
        config=config,
        run_manager=run_manager,
        metrics=metrics,
        scenario_id="S6_followup_chat",
    )
    ctx.chat_turns = [
        ChatTurnRecord(
            user_msg="q1",
            result=ChatStreamResult(ttft_ms=100.0, total_ms=800.0, tokens_in=50, tokens_out=20),
            chapter_idx=1,
            paragraph_idx=10,
        ),
        ChatTurnRecord(
            user_msg="q2",
            result=ChatStreamResult(ttft_ms=200.0, total_ms=1200.0, tokens_in=80, tokens_out=30),
            chapter_idx=1,
            paragraph_idx=10,
        ),
    ]

    # Simulate per-turn recording as verify_s5_chat_turn would do.
    for turn in ctx.chat_turns:
        result = turn.result
        metrics.record("chat.ttft_ms", result.ttft_ms or 0, unit="ms", scenario_id="S6_followup_chat")
        metrics.record("chat.total_ms", result.total_ms or 0, unit="ms", scenario_id="S6_followup_chat")
        metrics.record("chat.tokens.input", float(result.tokens_in or 0), unit="tokens", scenario_id="S6_followup_chat")
        metrics.record("chat.tokens.output", float(result.tokens_out or 0), unit="tokens", scenario_id="S6_followup_chat")

    await record_s5_chat_metrics(ctx, scenario_id="S6_followup_chat", step_id="record_metrics")

    agg = ctx.extras["chat_metric_aggregates"]["chat.ttft_ms"]
    assert agg["count"] == 2
    assert agg["p50"] == pytest.approx(150.0)
    assert agg["max"] == 200.0

    metric_names = {point.metric for point in metrics.points}
    assert "chat.turn_count" in metric_names
    assert "chat.ttft_ms.p50" in metric_names
    assert "chat.total_ms.p90" in metric_names
