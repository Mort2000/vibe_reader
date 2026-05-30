"""S3: Fast scroll and jump reading (V-08).

Rapidly changes reading position, jumps forward and back, and verifies
that throttling, deduplication, and final window resolution stay correct.
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
from ..flows.comments import (
    verify_s3_comment_reuse,
    verify_s3_final_window,
    verify_s3_jobs_stable,
)
from ..flows.corpus import setup_s3_fast_scroll
from ..flows.metrics import record_s3_scroll_metrics
from ..flows.reading import (
    fast_scroll_s3,
    jump_back_s3,
    jump_forward_s3,
    start_s3_reading_sse,
)

SCENARIO_ID = "S3_fast_scroll"


async def run_s3(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    builder = ScenarioBuilder(
        SCENARIO_ID,
        "Fast scroll and jump reading with window dedup validation",
    )

    builder.add_step(
        "setup_book",
        "Ensure corpus book is imported",
        _step_setup,
        timeout_s=90.0,
    )
    builder.add_step(
        "start_sse",
        "Subscribe to window SSE events",
        _step_start_sse,
        timeout_s=10.0,
    )
    builder.add_step(
        "fast_scroll",
        "Rapidly report many paragraph positions",
        _step_fast_scroll,
        timeout_s=60.0,
    )
    builder.add_step(
        "jump_forward",
        "Jump forward within chapter 1 to the middle probe paragraph",
        _step_jump_forward,
        timeout_s=30.0,
    )
    builder.add_step(
        "jump_back",
        "Jump back to a nearby earlier paragraph",
        _step_jump_back,
        timeout_s=30.0,
    )
    builder.add_step(
        "verify_comment_reuse",
        "Completed comments must be reused after jump-back",
        _step_verify_comment_reuse,
        timeout_s=20.0,
    )
    builder.add_step(
        "verify_final_window",
        "Final window must align with latest reading position",
        _step_verify_final_window,
        timeout_s=30.0,
    )
    builder.add_step(
        "verify_jobs_stable",
        "Running jobs must not overwrite the current window",
        _step_verify_jobs,
        timeout_s=20.0,
    )
    builder.add_step(
        "record_metrics",
        "Record scroll/jump dedup metrics",
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
        raise RuntimeError(f"S3 failed: {result.failure_summary}")


async def _step_setup(ctx: ScenarioContext) -> None:
    await setup_s3_fast_scroll(ctx, scenario_id=SCENARIO_ID, step_id="setup_book")


async def _step_start_sse(ctx: ScenarioContext) -> None:
    await start_s3_reading_sse(ctx, scenario_id=SCENARIO_ID, step_id="start_sse")


async def _step_fast_scroll(ctx: ScenarioContext) -> None:
    await fast_scroll_s3(ctx, scenario_id=SCENARIO_ID, step_id="fast_scroll")


async def _step_jump_forward(ctx: ScenarioContext) -> None:
    await jump_forward_s3(ctx, scenario_id=SCENARIO_ID, step_id="jump_forward")


async def _step_jump_back(ctx: ScenarioContext) -> None:
    await jump_back_s3(ctx, scenario_id=SCENARIO_ID, step_id="jump_back")


async def _step_verify_comment_reuse(ctx: ScenarioContext) -> None:
    await verify_s3_comment_reuse(
        ctx, scenario_id=SCENARIO_ID, step_id="verify_comment_reuse"
    )


async def _step_verify_final_window(ctx: ScenarioContext) -> None:
    await verify_s3_final_window(
        ctx, scenario_id=SCENARIO_ID, step_id="verify_final_window"
    )


async def _step_verify_jobs(ctx: ScenarioContext) -> None:
    await verify_s3_jobs_stable(ctx, scenario_id=SCENARIO_ID, step_id="verify_jobs_stable")


async def _step_record_metrics(ctx: ScenarioContext) -> None:
    await record_s3_scroll_metrics(ctx, scenario_id=SCENARIO_ID, step_id="record_metrics")
