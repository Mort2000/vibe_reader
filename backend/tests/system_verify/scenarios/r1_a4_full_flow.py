"""R1 A4: Full happy path with post-compaction streaming chat."""

from __future__ import annotations

from typing import Any

from ..audit_exporter import ChatAuditExporter, CommentAuditExporter
from ..compaction_audit import CompactionAuditExporter
from ..core.config import VerifyConfig
from ..core.context import ScenarioContext, create_scenario_context, publish_suite_ctx
from ..corpus import CorpusManager
from ..flows.audit import (
    export_a4_full_flow_audit,
    verify_backend_runtime,
)
from ..flows.chat import post_compaction_chat_a4
from ..flows.compaction import advance_for_a3_compaction
from ..flows.corpus import assert_happy_path_corpus, setup_a3_long_chapter
from ..flows.metrics import budget_check_a4_step
from ..flows.reading import start_reading_sse
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..core.scenario import ScenarioBuilder, ScenarioRunner

SCENARIO_ID = "R1_real_happy_path"


async def run(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    """R1 A4: compaction + post-compaction streaming chat with summary injection."""
    assert_happy_path_corpus(corpus)

    post_windows = config.params.long_flow.post_compaction_comment_windows
    builder = ScenarioBuilder(
        SCENARIO_ID,
        "Real LLM happy path — A4 full flow (compaction + post-compaction chat)",
    )
    builder.add_step(
        "verify_runtime",
        "Confirm verify mode, real LLM mode, model, and runtime config",
        _step_verify_runtime,
        timeout_s=10.0,
    )
    builder.add_step(
        "setup",
        "Import book and start at long chapter paragraph 0 for compaction reading",
        _step_setup,
        timeout_s=90.0,
    )
    builder.add_step(
        "start_sse",
        "Subscribe to window SSE at the long chapter",
        _step_start_sse,
        timeout_s=10.0,
    )
    builder.add_step(
        "advance_for_compaction",
        (
            f"Advance with batch {config.params.compaction_advance_batch_size} until "
            f"compaction completes, then {post_windows} post-compaction comment windows"
        ),
        _step_advance_for_compaction,
        timeout_s=float(config.params.max_wait_compaction_s) + 600.0,
    )
    builder.add_step(
        "post_compaction_chat",
        "Send streaming chat after compaction and verify summary injection",
        _step_post_compaction_chat,
        timeout_s=float(config.params.max_wait_chat_s) + 60.0,
    )
    builder.add_step(
        "export_audit",
        "Export compaction, comment, and chat audit samples",
        _step_export_audit,
        timeout_s=90.0,
    )
    builder.add_step(
        "budget_check",
        "Verify real LLM budget guardrails including A4 full flow coverage",
        _step_budget_check,
        timeout_s=10.0,
    )

    runner = ScenarioRunner(run_manager, config)
    ctx = create_scenario_context(
        run_manager=run_manager,
        config=config,
        metrics=metrics,
        corpus=corpus,
        scenario_id=SCENARIO_ID,
        suite_ctx=suite_ctx,
    )
    ctx.compaction_audit_exporter = CompactionAuditExporter(run_manager, config)
    ctx.comment_audit_exporter = CommentAuditExporter(run_manager, config)
    ctx.chat_audit_exporter = ChatAuditExporter(run_manager, config)

    try:
        result = await runner.run(builder, context=ctx)
    finally:
        session = ctx.reading_session
        if session:
            await session.stop()

    publish_suite_ctx(ctx, suite_ctx)

    if result.status.value != "passed":
        raise RuntimeError(f"R1 A4 full flow failed: {result.failure_summary}")


async def _step_verify_runtime(ctx: ScenarioContext) -> None:
    await verify_backend_runtime(
        ctx,
        scenario_id=SCENARIO_ID,
        step_id="verify_runtime",
        require_verify_endpoint=True,
        require_model_match=True,
    )


async def _step_setup(ctx: ScenarioContext) -> None:
    await setup_a3_long_chapter(ctx, scenario_id=SCENARIO_ID, step_id="setup")


async def _step_start_sse(ctx: ScenarioContext) -> None:
    await start_reading_sse(ctx, scenario_id=SCENARIO_ID, step_id="start_sse")


async def _step_advance_for_compaction(ctx: ScenarioContext) -> None:
    await advance_for_a3_compaction(
        ctx,
        scenario_id=SCENARIO_ID,
        step_id="advance_for_compaction",
    )


async def _step_post_compaction_chat(ctx: ScenarioContext) -> None:
    await post_compaction_chat_a4(
        ctx,
        scenario_id=SCENARIO_ID,
        step_id="post_compaction_chat",
    )


async def _step_export_audit(ctx: ScenarioContext) -> None:
    await export_a4_full_flow_audit(
        ctx,
        scenario_id=SCENARIO_ID,
        step_id="export_audit",
    )


async def _step_budget_check(ctx: ScenarioContext) -> None:
    await budget_check_a4_step(ctx, scenario_id=SCENARIO_ID, step_id="budget_check")
