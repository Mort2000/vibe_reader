"""S4: 128K tiered context and L3 chapter compaction (V-09).

Uses the ``long_context`` corpus probe, advances reading deep into a long
chapter, waits for compact_context jobs, and validates token budgets, L2 chunk
manifest stability, and ChapterCompressedSummary structure.
"""

from __future__ import annotations

from typing import Any

from ..client import TargetClient
from ..compaction_audit import CompactionAuditExporter
from ..config import VerifyConfig
from ..context_assertions import (
    assert_compaction_completed,
    assert_compaction_failure_does_not_block_comments,
    assert_l2_chunk_boundaries_stable,
    assert_reclaimed_l2_chunk_present,
    assert_token_budget,
    extract_l2_chunks,
    find_compaction_agent_runs,
    find_comment_agent_runs,
    record_context_metrics_from_verify,
)
from ..corpus import CorpusManager
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import ScenarioBuilder, ScenarioRunner, assert_that
from .common import (
    ReadingCursor,
    ReadingSession,
    ReadingTrace,
    advance_until_compaction,
    collect_latest_injected_contexts,
    ensure_imported_book,
    export_agent_audit_artifacts,
    fetch_verify_agent_runs,
    get_probe,
    load_chapter_paragraphs,
    load_chapters,
    merge_suite_ctx,
    publish_suite_ctx,
    record_verify_metrics_coverage,
)


async def run_s4(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus: CorpusManager,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    builder = ScenarioBuilder(
        "S4_long_context",
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

    runner = ScenarioRunner(run_manager, config)
    ctx: dict[str, Any] = {
        "run_manager": run_manager,
        "config": config,
        "metrics": metrics,
        "corpus": corpus,
        "scenario_id": "S4_long_context",
        "reading_trace": ReadingTrace(),
        "compaction_audit_exporter": CompactionAuditExporter(run_manager, config),
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
        raise RuntimeError(f"S4 failed: {result.failure_summary}")


async def _step_setup(ctx: dict[str, Any]) -> None:
    book_id, book = await ensure_imported_book(ctx)
    probe = get_probe(ctx["corpus"], "long_context")
    min_context_tokens = probe.requires_context_tokens_gte or 0
    assert_that.gte(
        min_context_tokens,
        100_000,
        label="long_context_probe_requires_context_tokens_gte",
    )
    ctx["book_id"] = book_id
    ctx["book"] = book
    ctx["probe"] = probe
    ctx["chapter_idx"] = probe.chapter_idx

    async with TargetClient(
        ctx["config"].target.base_url,
        ctx["run_manager"],
        "S4_long_context",
        "setup_book",
        context=ctx,
    ) as client:
        chapters = await load_chapters(ctx, book_id, client=client)

    chapter = next((ch for ch in chapters if ch.get("idx") == probe.chapter_idx), None)
    assert_that.is_not_none(chapter, "long_context chapter must exist")
    assert chapter is not None

    paragraphs = await load_chapter_paragraphs(ctx, book_id, probe.chapter_idx)
    assert_that.is_true(
        len(paragraphs) > 0, "long_context chapter must contain paragraphs"
    )
    assert_that.gte(
        paragraphs[-1]["paragraph_idx"],
        probe.paragraph_idx,
        label="long_context_probe_in_range",
    )

    ctx["chapters"] = chapters
    ctx["chapter_paragraphs"] = paragraphs
    ctx["reading_cursor"] = ReadingCursor(probe.chapter_idx, probe.paragraph_idx)


async def _step_start_sse(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    session = ReadingSession(
        config.target.base_url,
        ctx["run_manager"],
        "S4_long_context",
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
    cursor: ReadingCursor = ctx["reading_cursor"]
    chapters: list[dict[str, Any]] = ctx["chapters"]
    exporter: CompactionAuditExporter = ctx["compaction_audit_exporter"]

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S4_long_context",
        "advance_reading",
        context=ctx,
    ) as client:
        done_job, compaction_jobs, failed_job = await advance_until_compaction(
            client,
            ctx,
            ctx["book_id"],
            cursor,
            chapters,
            trace,
            session,
            scenario_id="S4_long_context",
            step_id="advance_reading",
            metrics=metrics,
            config=config,
        )

        agent_runs = await fetch_verify_agent_runs(
            client,
            ctx["run_manager"].run_id,
            scenario_id="S4_long_context",
        )

        comment_runs = find_comment_agent_runs(agent_runs)
        compaction_runs = find_compaction_agent_runs(agent_runs)
        contexts = await collect_latest_injected_contexts(
            client,
            ctx["run_manager"],
            scenario_id="S4_long_context",
        )
        if len(contexts) < 2 and comment_runs:
            first_ctx = (comment_runs[0].get("interaction") or comment_runs[0]).get(
                "injected_context"
            )
            last_ctx = (comment_runs[-1].get("interaction") or comment_runs[-1]).get(
                "injected_context"
            )
            if isinstance(first_ctx, dict):
                contexts.insert(0, first_ctx)
            if isinstance(last_ctx, dict) and last_ctx not in contexts:
                contexts.append(last_ctx)

        if len(contexts) >= 2:
            assert_l2_chunk_boundaries_stable(
                extract_l2_chunks(contexts[0]),
                extract_l2_chunks(contexts[-1]),
            )
            exporter.add_l2_manifest(
                scenario_id="S4_long_context",
                step_id="advance_reading",
                chapter_idx=cursor.chapter_idx,
                paragraph_idx=cursor.paragraph_idx,
                injected_context=contexts[0],
            )
            exporter.add_l2_manifest(
                scenario_id="S4_long_context",
                step_id="advance_reading_final",
                chapter_idx=cursor.chapter_idx,
                paragraph_idx=cursor.paragraph_idx,
                injected_context=contexts[-1],
            )

        for injected in contexts:
            assert_token_budget(injected, config)

        assert_compaction_failure_does_not_block_comments(
            comment_runs=comment_runs,
            trace=trace,
            failed_job=failed_job,
        )

        assert_compaction_completed(
            compaction_jobs=compaction_jobs,
            compaction_runs=compaction_runs,
            require_real=False,
        )

    ctx["compaction_job"] = done_job
    ctx["compaction_jobs"] = compaction_jobs
    ctx["compaction_failed_job"] = failed_job
    ctx["compaction_agent_runs"] = compaction_runs
    ctx["comment_agent_runs"] = comment_runs
    ctx["injected_contexts"] = contexts
    ctx["final_paragraph_idx"] = cursor.paragraph_idx


async def _step_verify_context(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    trace: ReadingTrace = ctx["reading_trace"]
    contexts: list[dict[str, Any]] = ctx.get("injected_contexts") or []

    assert_that.gte(
        trace.compaction_done_count + len(ctx.get("compaction_agent_runs") or []),
        1,
        label="compaction_observed",
    )

    for injected in contexts:
        assert_token_budget(injected, config)

    compaction_runs = ctx.get("compaction_agent_runs") or []
    if compaction_runs:
        interaction = compaction_runs[-1].get("interaction") or compaction_runs[-1]
        assert_token_budget(interaction.get("injected_context") or {}, config)

    assert_reclaimed_l2_chunk_present(
        injected_contexts=contexts,
        compaction_jobs=ctx.get("compaction_jobs") or [],
        compaction_runs=compaction_runs,
        completed_compactions=trace.completed_compactions,
    )


async def _step_export_audit(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    exporter: CompactionAuditExporter = ctx["compaction_audit_exporter"]
    model = config.effective_model() or config.real_llm.model

    for run in ctx.get("compaction_agent_runs") or []:
        exporter.add_compaction_run(
            run,
            scenario_id="S4_long_context",
            book=ctx["book"],
            chapter_idx=ctx["chapter_idx"],
            model=model or "",
            llm_mode=config.llm.mode,
            usage_source=config.usage_source,
        )

    counts = exporter.export()
    ctx["audit_export_counts"] = counts

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S4_long_context",
        "export_agent_audit",
        context=ctx,
    ) as audit_client:
        agent_counts = await export_agent_audit_artifacts(
            ctx,
            audit_client,
            scenario_id="S4_long_context",
            step_id="export_agent_audit",
        )
        ctx["audit_export_counts"].update(agent_counts)

        agent_runs = await fetch_verify_agent_runs(
            audit_client,
            ctx["run_manager"].run_id,
            scenario_id="S4_long_context",
        )
        for run in agent_runs:
            interaction = run.get("interaction") or run
            invocation_id = run.get("invocation_id") or interaction.get("invocation_id")
            if not invocation_id:
                continue
            exporter.add_prompt_manifest_entry(
                invocation_id=str(invocation_id),
                agent=str(run.get("agent") or interaction.get("agent") or ""),
                scenario_id="S4_long_context",
                step_id="export_agent_audit",
                prompt_path=f"audit/prompts/{invocation_id}.prompt.md",
                context_hash=str(interaction.get("context_hash") or ""),
                token_estimate=(interaction.get("injected_context") or {}).get(
                    "total_input_token_estimate"
                ),
            )
        ctx["audit_export_counts"].update(exporter.export())


async def _step_record_metrics(ctx: dict[str, Any]) -> None:
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    trace: ReadingTrace = ctx["reading_trace"]

    async with TargetClient(
        config.target.base_url,
        ctx["run_manager"],
        "S4_long_context",
        "record_metrics",
        context=ctx,
    ) as client:
        body, rec = await client.verify_metrics(
            ctx["run_manager"].run_id,
            scenario_id="S4_long_context",
        )
        if rec.status_code < 400 and body:
            record_context_metrics_from_verify(
                metrics,
                body,
                scenario_id="S4_long_context",
                step_id="record_metrics",
            )
            record_verify_metrics_coverage(
                metrics,
                body,
                scenario_id="S4_long_context",
                step_id="record_metrics",
            )

    metrics.record(
        "context.compaction.done_count",
        float(trace.compaction_done_count),
        unit="count",
        scenario_id="S4_long_context",
        step_id="record_metrics",
    )
