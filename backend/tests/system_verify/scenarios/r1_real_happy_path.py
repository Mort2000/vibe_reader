"""R1: Real LLM happy path — A2 comments coverage (V-18).

Runs only with explicit ``--suite real-happy-path --llm-mode real``.
Reading stop behavior is controlled by ``real_llm.long_flow.reading_stop_mode``:

- ``cross_chapter``: read from the corpus start probe forward until a chapter
  boundary is crossed, then stop.
- ``comment_windows`` (default): advance within the start chapter from the probe
  until at least ``min_comment_windows`` real comment windows complete.
"""

from __future__ import annotations

from typing import Any

from ..audit_exporter import CommentAuditExporter
from ..client import TargetClient
from ..config import (
    READING_STOP_COMMENT_WINDOWS,
    READING_STOP_CROSS_CHAPTER,
    VerifyConfig,
    validate_real_llm_config,
)
from ..contract import validate_comments_response, validate_no_span_in_comments
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import ScenarioBuilder, ScenarioRunner, StepAssertionError, assert_that
from .common import (
    ReadingCursor,
    ReadingSession,
    ReadingTrace,
    advance_reading,
    assert_comments_valid,
    chapter_by_idx,
    last_paragraph_idx,
    read_from_start_then_cross_chapter,
    collect_validation_failures,
    collect_usage_by_trace,
    ensure_imported_book,
    fetch_verify_jobs,
    get_probe,
    load_chapter_paragraphs,
    load_chapters,
    merge_suite_ctx,
    publish_suite_ctx,
    record_comment_metrics,
    record_verify_metrics_coverage,
    resolve_happy_path_start,
    sync_real_llm_tracker_from_verify_metrics,
    unique_trace_ids,
    verify_backend_runtime,
    wait_for_comments,
    wait_for_window_done,
    window_is_no_call,
)


def _advance_step_description(stop_mode: str, min_windows: int) -> str:
    if stop_mode == READING_STOP_CROSS_CHAPTER:
        return "Read forward from book start until a chapter boundary is crossed"
    return (
        f"Read forward from probe within the start chapter until at least "
        f"{min_windows} comment windows complete"
    )


def _advance_step_timeout_s(config: VerifyConfig, stop_mode: str, min_windows: int) -> float:
    max_wait = float(config.run.max_wait_comment_window_s)
    if stop_mode == READING_STOP_CROSS_CHAPTER:
        return max_wait + 240.0
    return max_wait * min_windows + 240.0


async def run_r1_a2_comments(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    if not config.is_real_llm:
        raise RuntimeError("R1 requires llm.mode=real")

    config_errors = validate_real_llm_config(config)
    if config_errors:
        raise RuntimeError("Real LLM config invalid: " + "; ".join(config_errors))

    corpus_errors = corpus.validate_happy_path_probe()
    if corpus_errors:
        raise RuntimeError(
            "Corpus does not satisfy happy_path_current requirements: "
            + "; ".join(corpus_errors)
        )

    long_flow = config.real_llm.long_flow
    stop_mode = long_flow.reading_stop_mode
    min_windows = long_flow.min_comment_windows
    builder = ScenarioBuilder(
        "R1_real_happy_path",
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
    ctx: dict[str, Any] = {
        "run_manager": run_manager,
        "config": config,
        "metrics": metrics,
        "corpus": corpus,
        "scenario_id": "R1_real_happy_path",
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
        raise RuntimeError(f"R1 A2 comments failed: {result.failure_summary}")


async def _step_verify_runtime(ctx: dict[str, Any]) -> None:
    await verify_backend_runtime(
        ctx,
        scenario_id="R1_real_happy_path",
        step_id="verify_runtime",
        require_verify_endpoint=True,
        require_model_match=True,
    )


async def _step_setup(ctx: dict[str, Any]) -> None:
    book_id, book = await ensure_imported_book(ctx)
    happy_probe = get_probe(ctx["corpus"], "happy_path_current")
    early_probe = get_probe(ctx["corpus"], "early")
    start_chapter, start_paragraph = resolve_happy_path_start(happy_probe, early_probe)

    ctx["book_id"] = book_id
    ctx["book"] = book
    ctx["probe"] = happy_probe
    ctx["start_chapter_idx"] = start_chapter
    ctx["start_paragraph_idx"] = start_paragraph
    ctx["chapter_idx"] = start_chapter

    config: VerifyConfig = ctx["config"]
    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "R1_real_happy_path",
        "setup",
        context=ctx,
    ) as client:
        chapters = await load_chapters(ctx, book_id, client=client)

    assert_that.is_true(len(chapters) >= 2, "Book must have at least two chapters")

    start_chapter_meta = next((ch for ch in chapters if ch.get("idx") == start_chapter), None)
    assert_that.is_not_none(start_chapter_meta, "Start chapter must exist in book metadata")
    assert start_chapter_meta is not None

    paragraphs = await load_chapter_paragraphs(ctx, book_id, start_chapter)
    assert_that.is_true(len(paragraphs) > 0, "Start chapter must contain paragraphs")
    assert_that.gte(
        paragraphs[-1]["paragraph_idx"],
        start_paragraph,
        label="start_paragraph_in_range",
    )

    ctx["chapters"] = chapters
    ctx["chapter_paragraphs"] = paragraphs
    ctx["reading_cursor"] = ReadingCursor(start_chapter, start_paragraph)
    ctx["comment_audit_exporter"] = CommentAuditExporter(ctx["run_manager"], ctx["config"])


async def _step_start_sse(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    session = ReadingSession(
        config.target.base_url,
        ctx["run_manager"],
        "R1_real_happy_path",
        ctx["book_id"],
        ctx["start_chapter_idx"],
    )
    await session.start()
    ctx["reading_session"] = session


async def _advance_until_cross_chapter(
    client: TargetClient,
    ctx: dict[str, Any],
    *,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    session: ReadingSession,
    metrics: MetricsAggregator,
    config: VerifyConfig,
) -> ReadingSession:
    session = await read_from_start_then_cross_chapter(
        client,
        ctx,
        ctx["book_id"],
        cursor,
        chapters,
        trace,
        session,
        scenario_id="R1_real_happy_path",
        step_id="advance_for_comments",
        metrics=metrics,
        warmup_paragraphs=24,
        delay_ms=config.run.progress_step_delay_ms,
    )
    ctx["reading_session"] = session
    ctx["final_paragraph_idx"] = cursor.paragraph_idx
    ctx["chapter_idx"] = cursor.chapter_idx
    return session


async def _advance_in_chapter_batch(
    client: TargetClient,
    ctx: dict[str, Any],
    *,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    metrics: MetricsAggregator,
    config: VerifyConfig,
    next_paragraph: int,
    batch_size: int,
) -> int:
    """Advance up to *batch_size* paragraphs within the cursor's current chapter."""
    chapter = chapter_by_idx(chapters, cursor.chapter_idx)
    if chapter is None:
        raise StepAssertionError(
            assertion="chapter_exists",
            message=f"Chapter {cursor.chapter_idx} not found in book metadata",
            actual={"chapter_idx": cursor.chapter_idx},
        )

    chapter_last = last_paragraph_idx(chapter)
    if next_paragraph > chapter_last:
        raise StepAssertionError(
            assertion="same_chapter_reading",
            message="Reached chapter end before min_comment_windows completed",
            actual={
                "chapter_idx": cursor.chapter_idx,
                "paragraph_idx": cursor.paragraph_idx,
                "chapter_last_paragraph_idx": chapter_last,
            },
        )

    end = min(next_paragraph + batch_size, chapter_last)
    last = await advance_reading(
        client,
        ctx,
        ctx["book_id"],
        cursor.chapter_idx,
        next_paragraph,
        end,
        trace,
        scenario_id="R1_real_happy_path",
        step_id="advance_for_comments",
        metrics=metrics,
        delay_ms=config.run.progress_step_delay_ms,
    )
    cursor.paragraph_idx = last
    ctx["final_paragraph_idx"] = last
    ctx["chapter_idx"] = cursor.chapter_idx
    return last + 1


async def _advance_until_comment_windows(
    client: TargetClient,
    ctx: dict[str, Any],
    *,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    session: ReadingSession,
    metrics: MetricsAggregator,
    config: VerifyConfig,
    min_windows: int,
) -> list[dict[str, Any]]:
    initial_batch = max(12, min_windows * 12)
    followup_batch = 12
    completed_windows: list[dict[str, Any]] = []
    next_paragraph = cursor.paragraph_idx

    next_paragraph = await _advance_in_chapter_batch(
        client,
        ctx,
        cursor=cursor,
        chapters=chapters,
        trace=trace,
        metrics=metrics,
        config=config,
        next_paragraph=next_paragraph,
        batch_size=initial_batch,
    )

    while len(completed_windows) < min_windows:
        window = await wait_for_window_done(
            client,
            session,
            ctx["book_id"],
            cursor.chapter_idx,
            cursor.paragraph_idx,
            float(config.run.max_wait_comment_window_s),
            trace,
        )
        if window and window.get("status") == "done":
            completed_windows.append(window)

        if len(completed_windows) >= min_windows:
            break

        next_paragraph = await _advance_in_chapter_batch(
            client,
            ctx,
            cursor=cursor,
            chapters=chapters,
            trace=trace,
            metrics=metrics,
            config=config,
            next_paragraph=next_paragraph,
            batch_size=followup_batch,
        )

    return completed_windows


async def _collect_chapter_comments_and_jobs(
    client: TargetClient,
    ctx: dict[str, Any],
    *,
    cursor: ReadingCursor,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_comments: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []

    for chapter_idx in cursor.visited_chapters:
        comments = await wait_for_comments(
            client,
            ctx["book_id"],
            chapter_idx,
            min_count=0,
            timeout_s=30.0,
        )
        body, rec = await client.list_comments(ctx["book_id"], chapter_idx)
        validate_comments_response(body, rec)
        validate_no_span_in_comments(body, rec)
        all_comments.extend(comments)

        chapter_jobs = await fetch_verify_jobs(
            client,
            ctx["book_id"],
            chapter_idx,
            scenario_id="R1_real_happy_path",
            step_id="advance_for_comments",
        )
        jobs.extend(chapter_jobs)

    return all_comments, jobs


async def _step_advance(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]
    session: ReadingSession = ctx["reading_session"]
    cursor: ReadingCursor = ctx["reading_cursor"]
    chapters: list[dict[str, Any]] = ctx["chapters"]
    long_flow = config.real_llm.long_flow
    stop_mode = long_flow.reading_stop_mode
    min_windows = long_flow.min_comment_windows

    completed_windows: list[dict[str, Any]] = []
    all_comments: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "R1_real_happy_path",
        "advance_for_comments",
        context=ctx,
    ) as client:
        if stop_mode == READING_STOP_CROSS_CHAPTER:
            session = await _advance_until_cross_chapter(
                client,
                ctx,
                cursor=cursor,
                chapters=chapters,
                trace=trace,
                session=session,
                metrics=metrics,
                config=config,
            )
        elif stop_mode == READING_STOP_COMMENT_WINDOWS:
            completed_windows = await _advance_until_comment_windows(
                client,
                ctx,
                cursor=cursor,
                chapters=chapters,
                trace=trace,
                session=session,
                metrics=metrics,
                config=config,
                min_windows=min_windows,
            )
        else:
            raise StepAssertionError(
                assertion="reading_stop_mode",
                message="Unsupported real_llm.long_flow.reading_stop_mode",
                actual={"reading_stop_mode": stop_mode},
            )

        all_comments, jobs = await _collect_chapter_comments_and_jobs(
            client,
            ctx,
            cursor=cursor,
        )

    if stop_mode == READING_STOP_CROSS_CHAPTER:
        assert_that.gte(
            cursor.chapters_crossed,
            1,
            label="cross_chapter_reading",
        )
    elif stop_mode == READING_STOP_COMMENT_WINDOWS:
        assert_that.gte(
            len(completed_windows),
            min_windows,
            label="real_comment_windows_completed",
        )

    for window in completed_windows:
        window_comments = [
            c
            for c in all_comments
            if window.get("id") is None or c.get("window_id") == window.get("id")
        ]
        assert_comments_valid(
            window_comments,
            window=window,
            allow_no_call=True,
            config=config,
        )

    ctx["completed_windows"] = completed_windows
    ctx["comments"] = all_comments
    ctx["verify_jobs"] = jobs
    ctx["chapters_crossed"] = cursor.chapters_crossed
    ctx["visited_chapters"] = list(cursor.visited_chapters)
    ctx["reading_stop_mode"] = stop_mode
    ctx["run_manager"].real_llm_tracker.phase_coverage["A2_comments"] = True

    record_comment_metrics(
        metrics,
        trace,
        scenario_id="R1_real_happy_path",
        step_id="advance_for_comments",
        jobs=jobs,
        comments=all_comments,
        window=completed_windows[-1] if completed_windows else None,
        config=config,
    )


async def _step_export_audit(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    exporter: CommentAuditExporter = ctx["comment_audit_exporter"]
    comments = ctx.get("comments") or []
    windows = ctx.get("completed_windows") or []
    jobs = ctx.get("verify_jobs") or []
    audit_window_limit = (
        config.real_llm.long_flow.min_comment_windows
        if ctx.get("reading_stop_mode") == READING_STOP_COMMENT_WINDOWS
        else len(windows)
    )

    trace_ids = unique_trace_ids(comments, jobs)
    tokens_by_trace: dict[str, dict[str, Any]] = {}
    latency_by_trace: dict[str, float] = {}
    trace_meta_by_trace_id: dict[str, dict[str, Any]] = {}
    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "R1_real_happy_path",
        "export_audit_samples",
        context=ctx,
    ) as client:
        (
            tokens_by_trace,
            latency_by_trace,
            trace_meta_by_trace_id,
        ) = await collect_usage_by_trace(client, trace_ids)

    for window in windows[:audit_window_limit]:
        chapter_idx = int(window.get("chapter_idx") or ctx["chapter_idx"])
        paragraphs = await load_chapter_paragraphs(ctx, ctx["book_id"], chapter_idx)
        window_comments = [
            c
            for c in comments
            if window.get("id") is None or c.get("window_id") == window.get("id")
        ]
        exporter.add_comments_from_window(
            window_comments,
            scenario_id="R1_real_happy_path",
            book=ctx["book"],
            chapter_idx=chapter_idx,
            window=window,
            paragraphs=paragraphs,
            model=config.effective_model() or config.real_llm.model,
            llm_mode="real",
            stub_profile=None,
            usage_source="provider" if config.metrics.collect_provider_usage else "estimate",
            latency_by_trace=latency_by_trace,
            tokens_by_trace=tokens_by_trace,
            trace_meta_by_trace_id=trace_meta_by_trace_id,
        )
        if not window_comments:
            exporter.record_window_status(
                scenario_id="R1_real_happy_path",
                book=ctx["book"],
                chapter_idx=chapter_idx,
                window=window,
                no_call=window_is_no_call(window, window_comments),
                validation_failures=collect_validation_failures(window_comments, window),
            )

    ndjson_count, md_count = exporter.export()
    ctx["audit_export_counts"] = {
        "comments_ndjson": ndjson_count,
        "comment_markdown": md_count,
    }


async def _step_budget_check(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "R1_real_happy_path",
        "budget_check",
        context=ctx,
    ) as client:
        verify_metrics = await sync_real_llm_tracker_from_verify_metrics(
            client,
            ctx["run_manager"],
            config,
        )
        if verify_metrics:
            record_verify_metrics_coverage(
                metrics,
                verify_metrics,
                scenario_id="R1_real_happy_path",
                step_id="budget_check",
            )

    tracker = ctx["run_manager"].real_llm_tracker
    tracker.check_budget(config)
    if tracker.budget_exceeded:
        raise StepAssertionError(
            assertion=tracker.budget_reason or "real_llm_budget_exceeded",
            message="Real LLM budget guardrail exceeded",
            actual={
                "call_count": tracker.call_count,
                "input_tokens": tracker.input_tokens,
                "output_tokens": tracker.output_tokens,
                "max_input_tokens_single": tracker.max_input_tokens_single,
                "max_output_tokens_single": tracker.max_output_tokens_single,
                "total_cost_usd": tracker.total_cost_usd,
            },
        )

    metrics.record(
        "real_llm.call_count",
        tracker.call_count,
        unit="count",
        scenario_id="R1_real_happy_path",
        step_id="budget_check",
    )
    metrics.record(
        "real_llm.cost_guardrail_status",
        1 if tracker.cost_guardrail_status == "enforced" else 0,
        unit="count",
        scenario_id="R1_real_happy_path",
        step_id="budget_check",
        tags={"status": tracker.cost_guardrail_status},
    )
    metrics.record(
        "reading.chapters_crossed",
        ctx.get("chapters_crossed", 0),
        unit="count",
        scenario_id="R1_real_happy_path",
        step_id="budget_check",
    )
