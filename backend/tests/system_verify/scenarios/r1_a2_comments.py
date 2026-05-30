"""R1 A2: Comments coverage happy path (stub and real LLM).

Reading stop behavior is controlled by ``params.long_flow.reading_stop_mode``:

- ``cross_chapter``: read through the entire start chapter from the probe while
  waiting for comment windows to keep pace, drain the chapter backlog, cross
  into the next chapter, then stop.
- ``comment_windows`` (default): advance within the start chapter from the probe
  until at least ``min_comment_windows`` real comment windows complete.
"""

from __future__ import annotations

from typing import Any

from ..core.config import (
    READING_STOP_CROSS_CHAPTER,
    VerifyConfig,
)
from ..core.context import ScenarioContext, create_scenario_context, publish_suite_ctx
from ..corpus import CorpusManager
from ..flows.audit import export_a2_comment_audit, verify_backend_runtime
from ..flows.comments import advance_for_a2_comments
from ..flows.corpus import assert_happy_path_corpus, setup_a2_reading_start
from ..flows.metrics import budget_check_step
from ..flows.reading import start_reading_sse
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..core.scenario import ScenarioBuilder, ScenarioRunner

SCENARIO_ID = "R1_real_happy_path"


def _advance_step_description(stop_mode: str, min_windows: int) -> str:
    if stop_mode == READING_STOP_CROSS_CHAPTER:
        return (
            "Read through the entire start chapter from the probe with comment "
            "agent sync, then cross into the next chapter"
        )
    return (
        f"Read forward from probe within the start chapter until at least "
        f"{min_windows} comment windows complete"
    )


def _advance_step_timeout_s(config: VerifyConfig, stop_mode: str, min_windows: int) -> float:
    max_wait = float(config.params.max_wait_comment_window_s)
    if stop_mode == READING_STOP_CROSS_CHAPTER:
        delay_s = config.params.progress_step_delay_ms / 1000.0
        return max(3600.0, 1500 * delay_s + max_wait * 6 + 300.0)
    return max_wait * min_windows + 240.0


async def run(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    assert_happy_path_corpus(corpus)

    long_flow = config.params.long_flow
    stop_mode = long_flow.reading_stop_mode
    min_windows = long_flow.min_comment_windows
    builder = ScenarioBuilder(
        SCENARIO_ID,
        "Real LLM happy path — configurable A2 comments reading flow",
    )
    builder.add_step(
        "verify_runtime",
        "Confirm verify mode, real LLM mode, model, and runtime config",
        _step_verify_runtime,
        timeout_s=10.0,
    )
    builder.add_step(
        "setup",
        "Import book, resolve start/end probes, and load chapter metadata",
        _step_setup,
        timeout_s=90.0,
    )
    builder.add_step(
        "start_sse",
        "Subscribe to window SSE at the reading start chapter",
        _step_start_sse,
        timeout_s=10.0,
    )
    builder.add_step(
        "advance_for_comments",
        _advance_step_description(stop_mode, min_windows),
        _step_advance,
        timeout_s=_advance_step_timeout_s(config, stop_mode, min_windows),
    )
    builder.add_step(
        "export_audit",
        "Export real comment audit samples",
        _step_export_audit,
        timeout_s=30.0,
    )
    builder.add_step(
        "budget_check",
        "Verify real LLM budget guardrails",
        _step_budget_check,
        timeout_s=5.0,
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

    try:
        result = await runner.run(builder, context=ctx)
    finally:
        session = ctx.reading_session
        if session:
            await session.stop()

    publish_suite_ctx(ctx, suite_ctx)

    if result.status.value != "passed":
        raise RuntimeError(f"R1 A2 comments failed: {result.failure_summary}")


async def _step_verify_runtime(ctx: ScenarioContext) -> None:
    await verify_backend_runtime(
        ctx,
        scenario_id=SCENARIO_ID,
        step_id="verify_runtime",
        require_verify_endpoint=True,
        require_model_match=True,
    )


async def _step_setup(ctx: ScenarioContext) -> None:
    await setup_a2_reading_start(ctx, scenario_id=SCENARIO_ID, step_id="setup")


async def _step_start_sse(ctx: ScenarioContext) -> None:
    await start_reading_sse(ctx, scenario_id=SCENARIO_ID, step_id="start_sse")


async def _step_advance(ctx: ScenarioContext) -> None:
    await advance_for_a2_comments(
        ctx,
        scenario_id=SCENARIO_ID,
        step_id="advance_for_comments",
    )


async def _step_export_audit(ctx: ScenarioContext) -> None:
    await export_a2_comment_audit(ctx, scenario_id=SCENARIO_ID, step_id="export_audit")


async def _step_budget_check(ctx: ScenarioContext) -> None:
    await budget_check_step(ctx, scenario_id=SCENARIO_ID, step_id="budget_check")
