"""S4: 128K tiered context and L3 chapter compaction (V-09).

Uses the ``long_context`` corpus probe in chapter 1, advances reading forward
(and crosses chapters when needed), waits for compact_context jobs on the start
chapter, and validates token budgets, L2 chunk manifest stability, and
ChapterCompressedSummary structure.
"""

from __future__ import annotations

from typing import Any

from ..core.config import VerifyConfig
from ..core.context import (
    ScenarioContext,
    create_scenario_context,
    publish_suite_ctx,
)
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..core.scenario import ScenarioBuilder, ScenarioRunner
from ..flows.audit import export_s4_compaction_audit
from ..flows.compaction import advance_s4_long_context, verify_s4_context
from ..flows.corpus import setup_s4_long_context
from ..flows.metrics import record_s4_context_metrics
from ..flows.reading import start_s4_reading_sse

SCENARIO_ID = "S4_long_context"


async def run_s4(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    builder = ScenarioBuilder(
        SCENARIO_ID,
        "128K tiered context with L3 chapter compaction",
    )
    builder.add_step(
        "setup_book",
        "Import corpus book and resolve long_context probe",
        _step_setup,
        timeout_s=90.0,
    )
    builder.add_step(
        "start_sse",
        "Subscribe to window and compaction SSE events",
        _step_start_sse,
        timeout_s=10.0,
    )
    builder.add_step(
        "advance_reading",
        "Advance from long_context probe until compaction completes",
        _step_advance,
        timeout_s=float(config.params.max_wait_compaction_s) + 120.0,
    )
    builder.add_step(
        "verify_context",
        "Validate token budget, L2 chunk stability, and summary structure",
        _step_verify_context,
        timeout_s=60.0,
    )
    builder.add_step(
        "export_audit",
        "Export compaction summary, L2 manifest, and agent audit artifacts",
        _step_export_audit,
        timeout_s=60.0,
    )
    builder.add_step(
        "record_metrics",
        "Record context and compaction metrics",
        _step_record_metrics,
        timeout_s=10.0,
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

    try:
        result = await runner.run(builder, context=ctx)
    finally:
        session = ctx.reading_session
        if session:
            await session.stop()

    publish_suite_ctx(ctx, suite_ctx)

    if result.status.value != "passed":
        raise RuntimeError(f"S4 failed: {result.failure_summary}")


async def _step_setup(ctx: ScenarioContext) -> None:
    await setup_s4_long_context(ctx, scenario_id=SCENARIO_ID, step_id="setup_book")


async def _step_start_sse(ctx: ScenarioContext) -> None:
    await start_s4_reading_sse(ctx, scenario_id=SCENARIO_ID, step_id="start_sse")


async def _step_advance(ctx: ScenarioContext) -> None:
    await advance_s4_long_context(ctx, scenario_id=SCENARIO_ID, step_id="advance_reading")


async def _step_verify_context(ctx: ScenarioContext) -> None:
    await verify_s4_context(ctx, scenario_id=SCENARIO_ID, step_id="verify_context")


async def _step_export_audit(ctx: ScenarioContext) -> None:
    await export_s4_compaction_audit(ctx, scenario_id=SCENARIO_ID, step_id="export_audit")


async def _step_record_metrics(ctx: ScenarioContext) -> None:
    await record_s4_context_metrics(ctx, scenario_id=SCENARIO_ID, step_id="record_metrics")
