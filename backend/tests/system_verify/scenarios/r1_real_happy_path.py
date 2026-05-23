"""R1: Real LLM happy path — A2 comments coverage (V-18).

Runs only with explicit ``--suite real-happy-path --llm-mode real``.
A2 subset covers continuous reading and at least two real comment windows.
"""

from __future__ import annotations

from typing import Any

from ..audit_exporter import CommentAuditExporter
from ..client import TargetClient
from ..config import VerifyConfig, validate_real_llm_config
from ..contract import validate_comments_response, validate_no_span_in_comments
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import ScenarioBuilder, ScenarioRunner, StepAssertionError, assert_that
from .common import (
    ReadingSession,
    ReadingTrace,
    advance_reading,
    assert_comments_valid,
    collect_validation_failures,
    ensure_imported_book,
    fetch_verify_jobs,
    get_probe,
    load_chapter_paragraphs,
    merge_suite_ctx,
    publish_suite_ctx,
    record_comment_metrics,
    verify_backend_runtime,
    wait_for_comments,
    wait_for_window_done,
    window_is_no_call,
)


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

    builder = ScenarioBuilder(
        "R1_real_happy_path",
        "Real LLM happy path — A2 comments coverage",
    )
    builder.add_step(
        "verify_runtime",
        "Confirm verify mode, real LLM mode, model, and runtime config",
        _step_verify_runtime,
        timeout_s=10.0,
    )
    builder.add_step("setup", "Import book and resolve happy_path_current", _step_setup, timeout_s=90.0)
    builder.add_step("start_sse", "Subscribe to window SSE", _step_start_sse, timeout_s=10.0)
    builder.add_step(
        "advance_for_comments",
        "Advance reading to trigger real comment windows",
        _step_advance,
        timeout_s=float(config.run.max_wait_comment_window_s) * 2 + 60.0,
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
    probe = get_probe(ctx["corpus"], "happy_path_current")
    ctx["book_id"] = book_id
    ctx["book"] = book
    ctx["probe"] = probe
    ctx["chapter_idx"] = probe.chapter_idx

    paragraphs = await load_chapter_paragraphs(ctx, book_id, probe.chapter_idx)
    assert_that.is_true(len(paragraphs) > 0, "Chapter must contain paragraphs")
    ctx["chapter_paragraphs"] = paragraphs
    ctx["comment_audit_exporter"] = CommentAuditExporter(ctx["run_manager"], ctx["config"])


async def _step_start_sse(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    session = ReadingSession(
        config.target.base_url,
        ctx["run_manager"],
        "R1_real_happy_path",
        ctx["book_id"],
        ctx["chapter_idx"],
    )
    await session.start()
    ctx["reading_session"] = session


async def _step_advance(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]
    session: ReadingSession = ctx["reading_session"]
    probe = ctx["probe"]
    min_windows = config.real_llm.long_flow.min_comment_windows

    start = probe.paragraph_idx
    last_idx = ctx["chapter_paragraphs"][-1]["paragraph_idx"]
    end = min(start + max(40, min_windows * 18), last_idx)

    completed_windows: list[dict[str, Any]] = []
    all_comments: list[dict[str, Any]] = []

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "R1_real_happy_path",
        "advance_for_comments",
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
            scenario_id="R1_real_happy_path",
            step_id="advance_for_comments",
            metrics=metrics,
            delay_ms=config.run.progress_step_delay_ms,
        )
        ctx["final_paragraph_idx"] = final

        while len(completed_windows) < min_windows:
            window = await wait_for_window_done(
                client,
                session,
                ctx["book_id"],
                ctx["chapter_idx"],
                ctx.get("final_paragraph_idx", final),
                float(config.run.max_wait_comment_window_s),
                trace,
            )
            if window:
                completed_windows.append(window)
                ctx["run_manager"].real_llm_tracker.record_call(
                    input_tokens=int(window.get("input_tokens") or 0),
                    output_tokens=int(window.get("output_tokens") or 0),
                    cost_usd=window.get("cost_usd"),
                    config=config,
                )

            if len(completed_windows) >= min_windows:
                break

            next_start = (completed_windows[-1].get("end_paragraph_idx") or final) + 1
            if next_start > last_idx:
                break
            final = await advance_reading(
                client,
                ctx,
                ctx["book_id"],
                ctx["chapter_idx"],
                next_start,
                min(next_start + 12, last_idx),
                trace,
                scenario_id="R1_real_happy_path",
                step_id="advance_for_comments",
                metrics=metrics,
                delay_ms=config.run.progress_step_delay_ms,
            )
            ctx["final_paragraph_idx"] = final

        comments = await wait_for_comments(
            client,
            ctx["book_id"],
            ctx["chapter_idx"],
            min_count=0,
            timeout_s=30.0,
        )
        body, rec = await client.list_comments(ctx["book_id"], ctx["chapter_idx"])
        validate_comments_response(body, rec)
        validate_no_span_in_comments(body, rec)
        all_comments = comments

        jobs = await fetch_verify_jobs(
            client,
            ctx["book_id"],
            ctx["chapter_idx"],
            scenario_id="R1_real_happy_path",
            step_id="advance_for_comments",
        )

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

    for window in windows[: config.real_llm.long_flow.min_comment_windows]:
        window_comments = [
            c
            for c in comments
            if window.get("id") is None or c.get("window_id") == window.get("id")
        ]
        exporter.add_comments_from_window(
            window_comments,
            scenario_id="R1_real_happy_path",
            book=ctx["book"],
            chapter_idx=ctx["chapter_idx"],
            window=window,
            paragraphs=ctx["chapter_paragraphs"],
            model=config.effective_model() or config.real_llm.model,
            llm_mode="real",
            stub_profile=None,
            usage_source="provider" if config.metrics.collect_provider_usage else "estimate",
        )
        if not window_comments and window_is_no_call(window, window_comments):
            exporter.record_window_status(
                scenario_id="R1_real_happy_path",
                book=ctx["book"],
                chapter_idx=ctx["chapter_idx"],
                window=window,
                no_call=True,
                validation_failures=collect_validation_failures(window_comments, window),
            )

    ndjson_count, md_count = exporter.export()
    ctx["audit_export_counts"] = {
        "comments_ndjson": ndjson_count,
        "comment_markdown": md_count,
    }


async def _step_budget_check(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
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

    metrics: MetricsAggregator = ctx["metrics"]
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
