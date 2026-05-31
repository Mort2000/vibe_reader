"""S6: Follow-up chat with recent history continuity (V-11)."""

from __future__ import annotations

from typing import Any

from ..core.config import VerifyConfig
from ..core.context import ScenarioContext, create_scenario_context, publish_suite_ctx
from ..corpus import CorpusManager
from ..flows.audit import export_chat_audit
from ..flows.chat import (
    advance_to_chat_position,
    run_s5_direct_chat,
    run_s6_followup_chat,
    verify_s6_recent_chat_context,
)
from ..flows.corpus import setup_s5_direct_chat
from ..flows.metrics import record_s5_chat_metrics
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..core.scenario import ScenarioBuilder, ScenarioRunner

SCENARIO_ID = "S6_followup_chat"


async def run_s6(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    builder = ScenarioBuilder(
        SCENARIO_ID,
        "Follow-up chat referencing the prior answer and recent history",
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
        "first_chat",
        "Send the initial direct chat question (S5 baseline)",
        _step_first_chat,
        timeout_s=float(config.params.max_wait_chat_s),
    )
    builder.add_step(
        "followup_chat",
        "Ask a follow-up that references the prior answer",
        _step_followup_chat,
        timeout_s=float(config.params.max_wait_chat_s),
    )
    builder.add_step(
        "verify_followup_context",
        "Confirm recent chat history is present in injected context",
        _step_verify_followup_context,
        timeout_s=30.0,
    )
    builder.add_step(
        "export_chat_audit",
        "Export continuous chat audit samples",
        _step_export_audit,
        timeout_s=30.0,
    )
    builder.add_step(
        "record_metrics",
        "Record chat continuity metrics",
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
        raise RuntimeError(f"S6 failed: {result.failure_summary}")


async def _step_setup(ctx: ScenarioContext) -> None:
    await setup_s5_direct_chat(ctx, scenario_id=SCENARIO_ID, step_id="setup_book")


async def _step_advance(ctx: ScenarioContext) -> None:
    await advance_to_chat_position(ctx, scenario_id=SCENARIO_ID, step_id="advance_reading")


async def _step_first_chat(ctx: ScenarioContext) -> None:
    await run_s5_direct_chat(ctx, scenario_id=SCENARIO_ID, step_id="first_chat")


async def _step_followup_chat(ctx: ScenarioContext) -> None:
    await run_s6_followup_chat(ctx, scenario_id=SCENARIO_ID, step_id="followup_chat")


async def _step_verify_followup_context(ctx: ScenarioContext) -> None:
    await verify_s6_recent_chat_context(
        ctx,
        scenario_id=SCENARIO_ID,
        step_id="verify_followup_context",
    )


async def _step_export_audit(ctx: ScenarioContext) -> None:
    await export_chat_audit(ctx, scenario_id=SCENARIO_ID, step_id="export_chat_audit")


async def _step_record_metrics(ctx: ScenarioContext) -> None:
    await record_s5_chat_metrics(ctx, scenario_id=SCENARIO_ID, step_id="record_metrics")
