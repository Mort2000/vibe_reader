"""S2: Continuous reading and paragraph comments (V-07).

Advances reading from an early probe, waits for comment windows,
validates persisted comments, and exports audit samples.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..audit_exporter import CommentAuditExporter
from ..client import TargetClient
from ..config import VerifyConfig
from ..contract import (
    validate_comments_response,
    validate_no_span_in_comments,
    validate_progress_response,
    validate_window_response,
)
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import ScenarioBuilder, ScenarioRunner, StepAssertionError, assert_that
from .common import (
    ReadingSession,
    ReadingTrace,
    advance_reading,
    assert_comments_valid,
    assert_reading_not_blocked,
    collect_validation_failures,
    collect_usage_by_trace,
    ensure_imported_book,
    export_agent_audit_artifacts,
    fetch_verify_jobs,
    get_probe,
    load_chapter_paragraphs,
    merge_suite_ctx,
    progress_update_was_deduped,
    publish_suite_ctx,
    record_comment_metrics,
    record_verify_metrics_coverage,
    unique_trace_ids,
    wait_for_comments,
    wait_for_window_done,
    window_is_no_call,
)


async def run_s2(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    builder = ScenarioBuilder(
        "S2_continuous_reading",
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

    runner = ScenarioRunner(run_manager, config)
    ctx: dict[str, Any] = {
        "run_manager": run_manager,
        "config": config,
        "metrics": metrics,
        "corpus": corpus,
        "scenario_id": "S2_continuous_reading",
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
        raise RuntimeError(f"S2 failed: {result.failure_summary}")


async def _step_setup(ctx: dict[str, Any]) -> None:
    book_id, book = await ensure_imported_book(ctx)
    probe = get_probe(ctx["corpus"], "early")
    ctx["book_id"] = book_id
    ctx["book"] = book
    ctx["probe"] = probe
    ctx["chapter_idx"] = probe.chapter_idx

    paragraphs = await load_chapter_paragraphs(ctx, book_id, probe.chapter_idx)
    assert_that.is_true(len(paragraphs) > 0, "Chapter must contain paragraphs")
    ctx["chapter_paragraphs"] = paragraphs

    if ctx.get("comment_audit_exporter") is None:
        ctx["comment_audit_exporter"] = CommentAuditExporter(
            ctx["run_manager"], ctx["config"]
        )


async def _step_start_sse(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    session = ReadingSession(
        config.target.base_url,
        ctx["run_manager"],
        "S2_continuous_reading",
        ctx["book_id"],
        ctx["chapter_idx"],
    )
    await session.start()
    ctx["reading_session"] = session


async def _step_advance(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    probe = ctx["probe"]
    trace: ReadingTrace = ctx["reading_trace"]

    start = probe.paragraph_idx
    paragraphs = ctx["chapter_paragraphs"]
    last_idx = paragraphs[-1]["paragraph_idx"]
    end = min(start + 12, last_idx)

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S2_continuous_reading",
        "advance_reading",
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
            scenario_id="S2_continuous_reading",
            step_id="advance_reading",
            metrics=metrics,
            delay_ms=config.params.progress_step_delay_ms,
        )
        ctx["final_paragraph_idx"] = final


async def _step_wait_window(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    session: ReadingSession = ctx["reading_session"]
    trace: ReadingTrace = ctx["reading_trace"]

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S2_continuous_reading",
        "wait_window_done",
        context=ctx,
    ) as client:
        window = await wait_for_window_done(
            client,
            session,
            ctx["book_id"],
            ctx["chapter_idx"],
            ctx["final_paragraph_idx"],
            float(config.params.max_wait_comment_window_s),
            trace,
        )
        session.ingest_events(trace)

        body, rec = await client.get_current_window(
            ctx["book_id"],
            ctx["chapter_idx"],
            paragraph_idx=ctx["final_paragraph_idx"],
        )
        validate_window_response(body, rec)
        ctx["completed_window"] = body.get("window")
        if window is not None and ctx["completed_window"] is None:
            ctx["completed_window"] = window


async def _step_verify_comments(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    session: ReadingSession = ctx.get("reading_session")
    trace: ReadingTrace = ctx["reading_trace"]

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S2_continuous_reading",
        "verify_comments",
        context=ctx,
    ) as client:
        comments = await wait_for_comments(
            client,
            ctx["book_id"],
            ctx["chapter_idx"],
            min_count=0,
            timeout_s=float(config.params.max_wait_comment_window_s),
        )

        body, rec = await client.list_comments(ctx["book_id"], ctx["chapter_idx"])
        validate_comments_response(body, rec)
        validate_no_span_in_comments(body, rec)
        window = ctx.get("completed_window")
        validation_failures = assert_comments_valid(
            comments,
            window=window,
            allow_no_call=True,
            config=config,
        )
        ctx["window_no_call"] = window_is_no_call(window, comments)
        ctx["validation_failures"] = validation_failures
        ctx["comments"] = comments

        if session:
            session.ingest_events(trace)
            session.record_comment_event_metrics(
                trace,
                ctx["metrics"],
                scenario_id="S2_continuous_reading",
                step_id="verify_comments",
            )


async def _step_verify_not_blocked(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S2_continuous_reading",
        "verify_not_blocked",
        context=ctx,
    ) as client:
        await assert_reading_not_blocked(
            client,
            ctx,
            ctx["book_id"],
            ctx["chapter_idx"],
            ctx["final_paragraph_idx"],
            trace,
            scenario_id="S2_continuous_reading",
            step_id="verify_not_blocked",
            metrics=metrics,
        )


async def _step_verify_dedup(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]
    session: ReadingSession = ctx["reading_session"]

    queued_before = trace.window_queued_count
    final = ctx["final_paragraph_idx"]

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S2_continuous_reading",
        "verify_window_dedup",
        context=ctx,
    ) as client:
        first, rec = await client.update_progress(
            ctx["book_id"], ctx["chapter_idx"], final, 0.35
        )
        validate_progress_response(first, rec)
        first_jobs = len(first.get("jobs") or [])
        await asyncio.sleep(1.1)
        second, rec = await client.update_progress(
            ctx["book_id"], ctx["chapter_idx"], final, 0.35
        )
        validate_progress_response(second, rec)
        second_jobs = len(second.get("jobs") or [])

        metrics.record_from_api_record(
            rec, scenario_id="S2_continuous_reading", step_id="verify_window_dedup"
        )

        if progress_update_was_deduped(first, second):
            trace.progress_dedup_count += 1

        session.ingest_events(trace)
        queued_after = trace.window_queued_count
        assert_that.is_true(
            queued_after == queued_before,
            "Identical progress should not enqueue additional windows",
        )
        if not progress_update_was_deduped(first, second) and (
            second_jobs > 0 or first_jobs > 0
        ):
            raise StepAssertionError(
                assertion="window_dedup",
                message=(
                    "Identical progress re-queued jobs without dedup markers; "
                    f"first_jobs={first_jobs}, second_jobs={second_jobs}"
                ),
                expected="deduped progress update",
                actual={"first": first, "second": second},
            )


async def _step_export_audit(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    exporter: CommentAuditExporter = ctx["comment_audit_exporter"]
    comments = ctx.get("comments") or []
    window = ctx.get("completed_window")

    model = config.effective_model() or config.real_llm.model
    jobs = ctx.get("verify_jobs") or []
    trace_ids = unique_trace_ids(comments, jobs)

    tokens_by_trace: dict[str, dict[str, Any]] = {}
    latency_by_trace: dict[str, float] = {}
    trace_meta_by_trace_id: dict[str, dict[str, Any]] = {}
    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S2_continuous_reading",
        "export_audit_samples",
        context=ctx,
    ) as client:
        (
            tokens_by_trace,
            latency_by_trace,
            trace_meta_by_trace_id,
        ) = await collect_usage_by_trace(client, trace_ids)

    exporter.add_comments_from_window(
        comments,
        scenario_id="S2_continuous_reading",
        book=ctx["book"],
        chapter_idx=ctx["chapter_idx"],
        window=window,
        paragraphs=ctx["chapter_paragraphs"],
        model=model or "",
        llm_mode=config.llm.mode,
        stub_profile=config.llm.stub_profile if not config.is_real_llm else None,
        usage_source=config.usage_source,
        latency_by_trace=latency_by_trace,
        tokens_by_trace=tokens_by_trace,
        trace_meta_by_trace_id=trace_meta_by_trace_id,
    )

    validation_failures = ctx.get("validation_failures") or collect_validation_failures(
        comments, window
    )

    if not comments:
        exporter.record_window_status(
            scenario_id="S2_continuous_reading",
            book=ctx["book"],
            chapter_idx=ctx["chapter_idx"],
            window=window,
            no_call=ctx.get("window_no_call", False),
            validation_failures=validation_failures,
        )
    elif validation_failures:
        exporter.record_window_status(
            scenario_id="S2_continuous_reading",
            book=ctx["book"],
            chapter_idx=ctx["chapter_idx"],
            window=window,
            no_call=False,
            validation_failures=validation_failures,
        )

    ndjson_count, md_count = exporter.export()
    ctx["audit_export_counts"] = {
        "comments_ndjson": ndjson_count,
        "comment_markdown": md_count,
        "no_call_window": ctx.get("window_no_call", False),
    }

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S2_continuous_reading",
        "export_agent_audit",
        context=ctx,
    ) as audit_client:
        agent_counts = await export_agent_audit_artifacts(
            ctx,
            audit_client,
            scenario_id="S2_continuous_reading",
            step_id="export_agent_audit",
        )
        ctx["audit_export_counts"].update(agent_counts)

    metrics: MetricsAggregator = ctx["metrics"]
    metrics.record(
        "audit.comments_exported",
        ndjson_count,
        unit="count",
        scenario_id="S2_continuous_reading",
        step_id="export_audit_samples",
    )


async def _step_record_metrics(ctx: dict[str, Any]) -> None:
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]
    config: VerifyConfig = ctx["config"]

    jobs: list[dict[str, Any]] = []
    verify_metrics: dict[str, Any] = {}
    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S2_continuous_reading",
        "record_metrics",
        context=ctx,
    ) as client:
        jobs = await fetch_verify_jobs(
            client,
            ctx["book_id"],
            ctx["chapter_idx"],
            scenario_id="S2_continuous_reading",
            step_id="record_metrics",
        )
        body, rec = await client.verify_metrics(ctx["run_manager"].run_id)
        if rec.status_code < 400:
            verify_metrics = body

    record_comment_metrics(
        metrics,
        trace,
        scenario_id="S2_continuous_reading",
        step_id="record_metrics",
        jobs=jobs,
        comments=ctx.get("comments") or [],
        window=ctx.get("completed_window"),
        config=config,
    )
    if verify_metrics:
        record_verify_metrics_coverage(
            metrics,
            verify_metrics,
            scenario_id="S2_continuous_reading",
            step_id="record_metrics",
        )
