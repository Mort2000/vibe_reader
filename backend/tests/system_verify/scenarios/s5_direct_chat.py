"""S5: Current-context direct chat without selection (V-10)."""

from __future__ import annotations

from typing import Any

from ..core.config import VerifyConfig
from ..core.context import ScenarioContext, create_scenario_context, publish_suite_ctx
from ..corpus import CorpusManager
from ..flows.audit import export_chat_audit
from ..flows.chat import advance_to_chat_position, run_s5_direct_chat
from ..flows.corpus import setup_s5_direct_chat
from ..flows.metrics import record_s5_chat_metrics
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..core.scenario import ScenarioBuilder, ScenarioRunner

SCENARIO_ID = "S5_direct_chat"


async def run_s5(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    builder = ScenarioBuilder(
        SCENARIO_ID,
        "Direct chat at current reading context without selection",
    )
    builder.add_step(
        "setup_book",
        "Import corpus book and resolve chat_live probe (P>180)",
        _step_setup,
        timeout_s=90.0,
    )
    builder.add_step(
        "advance_reading",
        "Advance reading cursor to the chat probe paragraph",
        _step_advance,
        timeout_s=30.0,
    )
    builder.add_step(
        "direct_chat",
        "Send a typical question via streaming chat API",
        _step_direct_chat,
        timeout_s=float(config.params.max_wait_chat_s),
    )
    builder.add_step(
        "export_chat_audit",
        "Export chat audit samples (V-15)",
        _step_export_audit,
        timeout_s=30.0,
    )
    builder.add_step(
        "record_metrics",
        "Record chat TTFT, latency, and token metrics",
        _step_record_metrics,
        timeout_s=5.0,
    )

    ctx = create_scenario_context(
        run_manager=run_manager,
        config=config,
        metrics=metrics,
        corpus=corpus,
        scenario_id=SCENARIO_ID,
        suite_ctx=suite_ctx,
    )

    runner = ScenarioRunner(run_manager, config)
    result = await runner.run(builder, context=ctx)
    publish_suite_ctx(ctx, suite_ctx)

    if result.status.value != "passed":
        raise RuntimeError(f"S5 failed: {result.failure_summary}")


async def _step_setup(ctx: ScenarioContext) -> None:
    await setup_s5_direct_chat(ctx, scenario_id=SCENARIO_ID, step_id="setup_book")


async def _step_advance(ctx: ScenarioContext) -> None:
    await advance_to_chat_position(ctx, scenario_id=SCENARIO_ID, step_id="advance_reading")


async def _step_direct_chat(ctx: ScenarioContext) -> None:
    await run_s5_direct_chat(ctx, scenario_id=SCENARIO_ID, step_id="direct_chat")


async def _step_export_audit(ctx: ScenarioContext) -> None:
    await export_chat_audit(ctx, scenario_id=SCENARIO_ID, step_id="export_chat_audit")


async def _step_record_metrics(ctx: ScenarioContext) -> None:
    await record_s5_chat_metrics(ctx, scenario_id=SCENARIO_ID, step_id="record_metrics")
