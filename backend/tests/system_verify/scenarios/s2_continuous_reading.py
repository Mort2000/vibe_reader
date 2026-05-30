"""S2: Continuous reading and paragraph comments (V-07).

Advances reading from an early probe, waits for comment windows,
validates persisted comments, and exports audit samples.
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
from ..flows.audit import export_s2_comment_audit
from ..flows.comments import (
    verify_s2_comments,
    verify_s2_window_dedup,
    wait_s2_window_done,
)
from ..flows.corpus import setup_s2_continuous_reading
from ..flows.metrics import record_s2_comment_metrics
from ..flows.reading import (
    advance_s2_reading,
    start_s2_reading_sse,
    verify_s2_reading_not_blocked,
)

SCENARIO_ID = "S2_continuous_reading"


async def run_s2(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    builder = ScenarioBuilder(
        SCENARIO_ID,
        "Continuous reading with paragraph comment generation",
    )

    builder.add_step(
        "setup_book",
        "Ensure corpus book is imported",
        _step_setup,
        timeout_s=90.0,
    )
    builder.add_step(
        "start_sse",
        "Subscribe to window and comment SSE events",
        _step_start_sse,
        timeout_s=10.0,
    )
    builder.add_step(
        "advance_reading",
        "Advance reading from early probe to trigger comment window",
        _step_advance,
        timeout_s=float(config.params.max_wait_comment_window_s) + 30.0,
    )
    builder.add_step(
        "wait_window_done",
        "Wait for comment window completion",
        _step_wait_window,
        timeout_s=float(config.params.max_wait_comment_window_s),
    )
    builder.add_step(
        "verify_comments",
        "Query comments API and validate contract",
        _step_verify_comments,
        timeout_s=30.0,
    )
    builder.add_step(
        "verify_not_blocked",
        "Confirm progress updates remain fast while comments exist",
        _step_verify_not_blocked,
        timeout_s=15.0,
    )
    builder.add_step(
        "verify_window_dedup",
        "Identical progress should not re-queue the same window",
        _step_verify_dedup,
        timeout_s=15.0,
    )
    builder.add_step(
        "export_audit_samples",
        "Export comment audit samples (V-15)",
        _step_export_audit,
        timeout_s=30.0,
    )
    builder.add_step(
        "record_metrics",
        "Record comment latency and dedup metrics",
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

    try:
        result = await runner.run(builder, context=ctx)
    finally:
        session = ctx.reading_session
        if session:
            await session.stop()

    publish_suite_ctx(ctx, suite_ctx)

    if result.status.value != "passed":
        raise RuntimeError(f"S2 failed: {result.failure_summary}")


async def _step_setup(ctx: ScenarioContext) -> None:
    await setup_s2_continuous_reading(ctx, scenario_id=SCENARIO_ID, step_id="setup_book")


async def _step_start_sse(ctx: ScenarioContext) -> None:
    await start_s2_reading_sse(ctx, scenario_id=SCENARIO_ID, step_id="start_sse")


async def _step_advance(ctx: ScenarioContext) -> None:
    await advance_s2_reading(ctx, scenario_id=SCENARIO_ID, step_id="advance_reading")


async def _step_wait_window(ctx: ScenarioContext) -> None:
    await wait_s2_window_done(ctx, scenario_id=SCENARIO_ID, step_id="wait_window_done")


async def _step_verify_comments(ctx: ScenarioContext) -> None:
    await verify_s2_comments(ctx, scenario_id=SCENARIO_ID, step_id="verify_comments")


async def _step_verify_not_blocked(ctx: ScenarioContext) -> None:
    await verify_s2_reading_not_blocked(
        ctx, scenario_id=SCENARIO_ID, step_id="verify_not_blocked"
    )


async def _step_verify_dedup(ctx: ScenarioContext) -> None:
    await verify_s2_window_dedup(ctx, scenario_id=SCENARIO_ID, step_id="verify_window_dedup")


async def _step_export_audit(ctx: ScenarioContext) -> None:
    await export_s2_comment_audit(
        ctx, scenario_id=SCENARIO_ID, step_id="export_audit_samples"
    )


async def _step_record_metrics(ctx: ScenarioContext) -> None:
    await record_s2_comment_metrics(ctx, scenario_id=SCENARIO_ID, step_id="record_metrics")
