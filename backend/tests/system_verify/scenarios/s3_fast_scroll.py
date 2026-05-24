"""S3: Fast scroll and jump reading (V-08).

Rapidly changes reading position, jumps forward and back, and verifies
that throttling, deduplication, and final window resolution stay correct.
"""

from __future__ import annotations

from typing import Any

from ..client import TargetClient
from ..config import VerifyConfig
from ..contract import validate_comments_response, validate_window_response
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import ScenarioBuilder, ScenarioRunner, StepAssertionError, assert_that
from .common import (
    ReadingSession,
    ReadingTrace,
    advance_reading,
    assert_comments_not_regenerated,
    ensure_imported_book,
    fetch_verify_jobs,
    get_probe,
    load_chapter_paragraphs,
    merge_suite_ctx,
    publish_suite_ctx,
    record_comment_metrics,
    save_jump_failure_context,
    update_progress,
    window_covers_paragraph,
)


async def run_s3(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    builder = ScenarioBuilder(
        "S3_fast_scroll",
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

    runner = ScenarioRunner(run_manager, config)
    ctx: dict[str, Any] = {
        "run_manager": run_manager,
        "config": config,
        "metrics": metrics,
        "corpus": corpus,
        "scenario_id": "S3_fast_scroll",
        "reading_trace": ReadingTrace(),
    }
    merge_suite_ctx(ctx, suite_ctx)

    try:
        result = await runner.run(builder, context=ctx)
    finally:
        session = ctx.get("reading_session")
        if session:
            await session.stop()

    publish_suite_ctx(ctx, suite_ctx)

    if result.status.value != "passed":
        raise RuntimeError(f"S3 failed: {result.failure_summary}")


async def _step_setup(ctx: dict[str, Any]) -> None:
    book_id, book = await ensure_imported_book(ctx)
    early = get_probe(ctx["corpus"], "early")
    middle = get_probe(ctx["corpus"], "middle")
    ctx["book_id"] = book_id
    ctx["book"] = book
    ctx["early_probe"] = early
    ctx["middle_probe"] = middle
    ctx["chapter_idx"] = early.chapter_idx

    paragraphs = await load_chapter_paragraphs(ctx, book_id, early.chapter_idx)
    assert_that.is_true(len(paragraphs) > 0, "Chapter must contain paragraphs")
    ctx["chapter_paragraphs"] = paragraphs


async def _step_start_sse(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    session = ReadingSession(
        config.target.base_url,
        ctx["run_manager"],
        "S3_fast_scroll",
        ctx["book_id"],
        ctx["chapter_idx"],
    )
    await session.start()
    ctx["reading_session"] = session


async def _step_fast_scroll(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]
    early = ctx["early_probe"]

    end = min(early.paragraph_idx + 25, ctx["chapter_paragraphs"][-1]["paragraph_idx"])
    start = max(0, early.paragraph_idx - 5)

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S3_fast_scroll",
        "fast_scroll",
        context=ctx,
    ) as client:
        final = await advance_reading(
            client,
            ctx,
            ctx["book_id"],
            ctx["chapter_idx"],
            start,
            end,
            trace,
            scenario_id="S3_fast_scroll",
            step_id="fast_scroll",
            metrics=metrics,
            delay_ms=0,
        )
        ctx["fast_scroll_end"] = final


async def _step_jump_forward(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]
    middle = ctx["middle_probe"]

    if middle.chapter_idx != ctx["chapter_idx"]:
        ctx["chapter_idx"] = middle.chapter_idx
        ctx["chapter_paragraphs"] = await load_chapter_paragraphs(
            ctx, ctx["book_id"], middle.chapter_idx
        )

    target = middle.paragraph_idx
    last_idx = ctx["chapter_paragraphs"][-1]["paragraph_idx"]
    target = min(target, last_idx)

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S3_fast_scroll",
        "jump_forward",
        context=ctx,
    ) as client:
        await update_progress(
            client,
            ctx,
            ctx["book_id"],
            ctx["chapter_idx"],
            target,
            0.2,
            trace,
            scenario_id="S3_fast_scroll",
            step_id="jump_forward",
            metrics=metrics,
        )
        ctx["jump_forward_paragraph"] = target

        body, rec = await client.list_comments(ctx["book_id"], ctx["chapter_idx"])
        validate_comments_response(body, rec)
        items = body.get("items") or []
        ctx["comments_before_jump_back"] = {
            item["paragraph_idx"]: item["id"]
            for item in items
            if item.get("paragraph_idx") is not None and item.get("id") is not None
        }
        session: ReadingSession = ctx["reading_session"]
        trace: ReadingTrace = ctx["reading_trace"]
        session.ingest_events(trace)
        ctx["comment_event_count_before_jump_back"] = len(trace.comment_events)


async def _step_jump_back(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]
    early = ctx["early_probe"]

    if early.chapter_idx != ctx["chapter_idx"]:
        ctx["chapter_idx"] = early.chapter_idx
        ctx["chapter_paragraphs"] = await load_chapter_paragraphs(
            ctx, ctx["book_id"], early.chapter_idx
        )

    back_target = min(
        early.paragraph_idx + 5,
        ctx["chapter_paragraphs"][-1]["paragraph_idx"],
    )

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S3_fast_scroll",
        "jump_back",
        context=ctx,
    ) as client:
        await update_progress(
            client,
            ctx,
            ctx["book_id"],
            ctx["chapter_idx"],
            back_target,
            0.6,
            trace,
            scenario_id="S3_fast_scroll",
            step_id="jump_back",
            metrics=metrics,
        )
        ctx["final_paragraph_idx"] = back_target


async def _step_verify_comment_reuse(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    session: ReadingSession = ctx["reading_session"]
    trace: ReadingTrace = ctx["reading_trace"]
    comments_before: dict[int, int] = ctx.get("comments_before_jump_back") or {}
    event_count_before = ctx.get("comment_event_count_before_jump_back", 0)

    if not comments_before:
        ctx["comment_reuse_skipped"] = True
        return

    session.ingest_events(trace)
    new_events = trace.comment_events[event_count_before:]

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S3_fast_scroll",
        "verify_comment_reuse",
        context=ctx,
    ) as client:
        await assert_comments_not_regenerated(
            client,
            ctx["book_id"],
            ctx["chapter_idx"],
            comments_before,
            new_events,
        )


async def _step_verify_final_window(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    session: ReadingSession = ctx["reading_session"]
    trace: ReadingTrace = ctx["reading_trace"]
    expected = ctx["final_paragraph_idx"]

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S3_fast_scroll",
        "verify_final_window",
        context=ctx,
    ) as client:
        progress, _ = await client.get_progress(ctx["book_id"])
        assert_that.equal(
            progress.get("paragraph_idx"),
            expected,
            label="saved_progress_paragraph_idx",
        )

        body, rec = await client.get_current_window(
            ctx["book_id"],
            ctx["chapter_idx"],
            paragraph_idx=expected,
        )
        validate_window_response(body, rec)
        window = body.get("window")
        ctx["final_window"] = window

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S3_fast_scroll", step_id="verify_final_window"
        )

        if window is None:
            save_jump_failure_context(
                ctx,
                book_id=ctx["book_id"],
                chapter_idx=ctx["chapter_idx"],
                expected_paragraph=expected,
                window=window,
                jobs=await fetch_verify_jobs(
                    client,
                    ctx["book_id"],
                    ctx["chapter_idx"],
                    scenario_id="S3_fast_scroll",
                    step_id="verify_final_window",
                ),
            )
            raise StepAssertionError(
                assertion="window_exists",
                message="Expected a current window after jump reading",
                actual=body,
            )

        covers = window_covers_paragraph(window, expected)
        frontier = window.get("assistant_frontier_paragraph_idx")
        frontier_ok = frontier is None or frontier >= expected

        if not covers and not frontier_ok:
            jobs = await fetch_verify_jobs(
                client,
                ctx["book_id"],
                ctx["chapter_idx"],
                scenario_id="S3_fast_scroll",
                step_id="verify_final_window",
            )
            save_jump_failure_context(
                ctx,
                book_id=ctx["book_id"],
                chapter_idx=ctx["chapter_idx"],
                expected_paragraph=expected,
                window=window,
                jobs=jobs,
            )
            raise StepAssertionError(
                assertion="window_aligns_with_reading",
                message=(
                    f"Window [{window.get('start_paragraph_idx')}, "
                    f"{window.get('end_paragraph_idx')}] does not cover paragraph "
                    f"{expected} (frontier={frontier})"
                ),
                expected=expected,
                actual=window,
            )

        session.ingest_events(trace)


async def _step_verify_jobs(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    trace: ReadingTrace = ctx["reading_trace"]
    window = ctx.get("final_window") or {}
    current_window_id = window.get("id")

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S3_fast_scroll",
        "verify_jobs_stable",
        context=ctx,
    ) as client:
        jobs = await fetch_verify_jobs(
            client,
            ctx["book_id"],
            ctx["chapter_idx"],
            scenario_id="S3_fast_scroll",
            step_id="verify_jobs_stable",
        )
        ctx["verify_jobs_snapshot"] = jobs

        running = [j for j in jobs if j.get("status") == "running"]
        for job in running:
            job_window_id = job.get("window_id")
            if (
                current_window_id is not None
                and job_window_id is not None
                and int(job_window_id) != int(current_window_id)
            ):
                trace.stale_job_ignored_count += 1

        # After jump-back, latest window id should remain authoritative.
        if current_window_id is not None:
            latest_body, rec = await client.get_current_window(
                ctx["book_id"],
                ctx["chapter_idx"],
                paragraph_idx=ctx["final_paragraph_idx"],
            )
            validate_window_response(latest_body, rec)
            latest_window = latest_body.get("window") or {}
            assert_that.equal(
                latest_window.get("id"),
                current_window_id,
                label="current_window_id_stable_after_jobs_check",
            )


async def _step_record_metrics(ctx: dict[str, Any]) -> None:
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]
    record_comment_metrics(
        metrics,
        trace,
        scenario_id="S3_fast_scroll",
        step_id="record_metrics",
    )

    if ctx.get("jump_failure_context"):
        ctx["run_manager"].write_ndjson(
            "audit/jump_failure_context.ndjson",
            [ctx["jump_failure_context"]],
        )
