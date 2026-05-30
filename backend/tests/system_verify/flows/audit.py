"""Verify runtime, agent runs, jobs, and audit export glue."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..audit_exporter import CommentAuditExporter
from ..core.client_factory import TargetClient
from ..compaction_audit import CompactionAuditExporter
from ..core.config import (
    READING_STOP_COMMENT_WINDOWS,
    VerifyConfig,
)
from ..core.context import ScenarioContext
from ..assertions.api_contracts import validate_comments_response, validate_no_span_in_comments
from ..core.run_manager import RunManager
from ..core.scenario import StepAssertionError, assert_that
from ..profiles.registry import profile_from_param_set

from .corpus import load_chapter_paragraphs
from .metrics import collect_usage_by_trace, unique_trace_ids

logger = logging.getLogger(__name__)


def _compaction_job_id(job: dict[str, Any]) -> int:
    return int(job.get("id") or job.get("job_id") or 0)


def _filter_compaction_jobs_for_audit(
    jobs: list[dict[str, Any]], *, min_job_id: int = 0
) -> list[dict[str, Any]]:
    if min_job_id <= 0:
        return jobs
    return [job for job in jobs if _compaction_job_id(job) > min_job_id]


def append_compaction_jobs_audit(
    run_manager: RunManager,
    jobs: list[dict[str, Any]],
    *,
    scenario_id: str,
    min_job_id: int = 0,
) -> None:
    """Append filtered compaction job records for summary reporting."""
    filtered = _filter_compaction_jobs_for_audit(jobs, min_job_id=min_job_id)
    if not filtered:
        return

    audit_dir = run_manager.base_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / "compaction_jobs.ndjson"
    summary = {"done": 0, "skipped": 0, "failed": 0, "other": 0}
    for job in filtered:
        status = str(job.get("status") or "other")
        if status in summary:
            summary[status] += 1
        else:
            summary["other"] += 1

    row = {
        "scenario_id": scenario_id,
        "min_job_id": min_job_id,
        "job_count": len(filtered),
        "summary": summary,
        "jobs": filtered,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def audit_stub_profile(config: VerifyConfig) -> str | None:
    """Return stub profile label for audit exports; None when using real LLM."""
    return config.llm.stub_profile if config.llm.mode == "stub" else None


def audit_export_metadata(config: VerifyConfig) -> dict[str, Any]:
    """Profile-driven audit export labels (llm mode, stub profile, usage source)."""
    profile = profile_from_param_set(config.params)
    return {
        "model": config.effective_model() or config.real_llm.model,
        "llm_mode": profile.llm_mode,
        "stub_profile": audit_stub_profile(config),
        "usage_source": audit_usage_source(config),
    }


def audit_usage_source(config: VerifyConfig) -> str:
    return "provider" if config.metrics.collect_provider_usage else "estimate"


async def verify_backend_runtime(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "verify_runtime",
    require_verify_endpoint: bool = False,
    require_model_match: bool = False,
) -> None:
    """Call /verify/runtime and validate verify mode plus LLM configuration."""
    run_manager = ctx.run_manager
    config = ctx.config
    metrics = ctx.metrics

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.verify_runtime()
        if rec.status_code == 404:
            if require_verify_endpoint:
                raise StepAssertionError(
                    assertion="verify_runtime_available",
                    message="Verify runtime endpoint required but returned 404",
                    actual={"status_code": rec.status_code},
                )
            ctx.extras["verify_mode_active"] = False
            return

        assert_that.is_true(
            body.get("verify_mode", False),
            "Verify mode should be enabled",
        )

        llm = body.get("llm", {})
        if llm:
            assert_that.not_contains(
                str(llm), "sk-", "Verify runtime must not expose api_key"
            )

        backend_mode = llm.get("mode")
        if backend_mode is not None:
            assert_that.equal(
                backend_mode,
                config.llm.mode,
                label="backend_llm_mode_matches_verify_config",
            )

        if require_model_match:
            expected_model = config.effective_model()
            backend_model = llm.get("model")
            assert_that.is_not_none(
                backend_model, "verify runtime should expose llm.model"
            )
            if expected_model:
                assert_that.equal(
                    backend_model,
                    expected_model,
                    label="backend_llm_model_matches_verify_config",
                )

        config_hash = body.get("config_hash") or llm.get("config_hash")
        if config_hash is not None:
            metrics.record(
                "verify.runtime.config_hash_available",
                1,
                unit="count",
                scenario_id=scenario_id,
                step_id=step_id,
            )
            ctx.extras["backend_config_hash"] = config_hash
        else:
            metrics.record(
                "verify.runtime.config_hash_available",
                0,
                unit="count",
                scenario_id=scenario_id,
                step_id=step_id,
            )

        ctx.extras["verify_mode_active"] = True
        ctx.extras["backend_version"] = body.get("app_version")
        ctx.verify_runtime = body
        run_manager.set_backend_version(body.get("app_version"))

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)

async def fetch_verify_agent_runs(
    client: TargetClient,
    run_id: str,
    *,
    scenario_id: str | None = None,
    step_id: str = "",
) -> list[dict[str, Any]]:
    body, rec = await client.verify_agent_runs(run_id, scenario_id=scenario_id)
    if rec.status_code == 404:
        logger.warning(
            "%s: GET /api/verify/agent-runs returned 404 — verify mode likely disabled",
            step_id or "fetch_verify_agent_runs",
        )
        return []
    if rec.status_code >= 400:
        logger.warning(
            "%s: GET /api/verify/agent-runs returned HTTP %s for run_id=%s",
            step_id or "fetch_verify_agent_runs",
            rec.status_code,
            run_id,
        )
        return []
    return body.get("items") or []

async def export_agent_audit_artifacts(
    ctx: ScenarioContext,
    client: TargetClient,
    *,
    scenario_id: str,
    step_id: str,
) -> dict[str, int]:
    from ..agent_audit_exporter import (
        AgentAuditExporter,
        assert_agent_audit_artifacts,
        enrich_comment_records_with_agent_refs,
    )

    config: VerifyConfig = ctx.config
    run_manager: RunManager = ctx.run_manager
    if not config.audit.enabled:
        return {}

    agent_runs = await fetch_verify_agent_runs(
        client,
        run_manager.run_id,
        scenario_id=scenario_id,
        step_id=step_id,
    )
    if not agent_runs:
        agent_runs = await fetch_verify_agent_runs(
            client,
            run_manager.run_id,
            scenario_id=None,
            step_id=step_id,
        )

    exporter = AgentAuditExporter(run_manager, config)
    counts = exporter.export_from_agent_runs(agent_runs)

    comments_path = run_manager.base_dir / "audit" / "comments.ndjson"
    enrich_comment_records_with_agent_refs(comments_path, exporter.trace_to_invocation)

    failures = assert_agent_audit_artifacts(run_manager.base_dir)
    if failures and config.audit.write_markdown_report:
        raise StepAssertionError(
            assertion="agent_audit",
            message="; ".join(failures[:3]),
            expected="complete agent audit artifacts",
            actual={"failures": failures},
        )

    ctx.extras["agent_audit_counts"] = counts
    return counts

async def fetch_verify_jobs(
    client: TargetClient,
    book_id: int,
    chapter_idx: int,
    *,
    scenario_id: str = "",
    step_id: str = "",
    run_id: str | None = None,
    job_type: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "limit": 200,
    }
    if run_id:
        params["run_id"] = run_id
    if job_type:
        params["job_type"] = job_type
    if status:
        params["status"] = status
    body, rec = await client.verify_jobs(params=params)
    if rec.status_code == 404:
        logger.warning(
            "%s/%s: GET /api/verify/jobs returned 404 — verify mode likely disabled; "
            "job latency metrics will be unavailable",
            scenario_id or "verify",
            step_id or "fetch_verify_jobs",
        )
        return []

    items = body.get("items") or []
    if not items:
        logger.warning(
            "%s/%s: GET /api/verify/jobs returned no items for book_id=%s chapter_idx=%s",
            scenario_id or "verify",
            step_id or "fetch_verify_jobs",
            book_id,
            chapter_idx,
        )
    return items

def filter_agent_runs_for_chapter(
    agent_runs: list[dict[str, Any]], chapter_idx: int
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for run in agent_runs:
        interaction = run.get("interaction") or run
        run_chapter_idx = run.get("chapter_idx")
        if run_chapter_idx is None:
            run_chapter_idx = interaction.get("chapter_idx")
        if int(run_chapter_idx if run_chapter_idx is not None else -1) == chapter_idx:
            filtered.append(run)
    return filtered

async def collect_latest_injected_contexts(
    client: TargetClient,
    run_manager: RunManager,
    *,
    scenario_id: str | None = None,
) -> list[dict[str, Any]]:
    from ..assertions.context import find_comment_agent_runs, find_compaction_agent_runs

    agent_runs = await fetch_verify_agent_runs(
        client,
        run_manager.run_id,
        scenario_id=scenario_id,
    )
    contexts: list[dict[str, Any]] = []
    for run in find_comment_agent_runs(agent_runs) + find_compaction_agent_runs(
        agent_runs
    ):
        interaction = run.get("interaction") or run
        injected = interaction.get("injected_context")
        if isinstance(injected, dict):
            contexts.append(injected)
    return contexts


async def export_a2_comment_audit(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "export_audit",
) -> None:
    """Export comment audit samples and agent artifacts for A2 coverage."""
    from .comments import collect_validation_failures, window_is_no_call

    config = ctx.config
    exporter: CommentAuditExporter = ctx.comment_audit_exporter
    comments = ctx.comments or []
    windows = ctx.completed_windows or []
    jobs = ctx.verify_jobs or []
    audit_window_limit = (
        config.params.long_flow.min_comment_windows
        if ctx.extras.get("reading_stop_mode") == READING_STOP_COMMENT_WINDOWS
        else len(windows)
    )

    trace_ids = unique_trace_ids(comments, jobs)
    tokens_by_trace: dict[str, dict[str, Any]] = {}
    latency_by_trace: dict[str, float] = {}
    trace_meta_by_trace_id: dict[str, dict[str, Any]] = {}
    async with TargetClient(
        config.target.base_url,
        ctx.run_manager,
        scenario_id,
        "export_audit_samples",
        context=ctx,
    ) as client:
        (
            tokens_by_trace,
            latency_by_trace,
            trace_meta_by_trace_id,
        ) = await collect_usage_by_trace(client, trace_ids)
    assert ctx.book_id is not None
    for window in windows[:audit_window_limit]:
        chapter_idx = int(window.get("chapter_idx") or ctx.chapter_idx)
        paragraphs = await load_chapter_paragraphs(ctx, ctx.book_id, chapter_idx)
        window_comments = [
            c
            for c in comments
            if window.get("id") is None or c.get("window_id") == window.get("id")
        ]
        exporter.add_comments_from_window(
            window_comments,
            scenario_id=scenario_id,
            book=ctx.book,
            chapter_idx=chapter_idx,
            window=window,
            paragraphs=paragraphs,
            model=config.effective_model() or config.real_llm.model,
            llm_mode=config.llm.mode,
            stub_profile=audit_stub_profile(config),
            usage_source=audit_usage_source(config),
            latency_by_trace=latency_by_trace,
            tokens_by_trace=tokens_by_trace,
            trace_meta_by_trace_id=trace_meta_by_trace_id,
        )
        if not window_comments:
            exporter.record_window_status(
                scenario_id=scenario_id,
                book=ctx.book,
                chapter_idx=chapter_idx,
                window=window,
                no_call=window_is_no_call(window, window_comments),
                validation_failures=collect_validation_failures(
                    window_comments, window
                ),
            )

    ndjson_count, md_count = exporter.export()
    ctx.extras["audit_export_counts"] = {
        "comments_ndjson": ndjson_count,
        "comment_markdown": md_count,
    }

    async with TargetClient(
        config.target.base_url,
        ctx.run_manager,
        scenario_id,
        "export_agent_audit",
        context=ctx,
    ) as audit_client:
        agent_counts = await export_agent_audit_artifacts(
            ctx,
            audit_client,
            scenario_id=scenario_id,
            step_id="export_agent_audit",
        )
        ctx.extras["audit_export_counts"].update(agent_counts)


async def export_a3_compaction_audit(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "export_compaction_audit",
) -> None:
    """Export compaction audit samples and agent artifacts for A3 coverage."""
    config: VerifyConfig = ctx.config
    exporter: CompactionAuditExporter = ctx.compaction_audit_exporter
    compaction_runs = ctx.compaction_agent_runs or []

    async with TargetClient(
        config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        (
            tokens_by_trace,
            latency_by_trace,
            _trace_meta,
        ) = await collect_usage_by_trace(
            client,
            [
                str(run.get("trace_id") or "")
                for run in compaction_runs
                if run.get("trace_id")
            ],
        )
    for run in compaction_runs:
        trace_id = str(run.get("trace_id") or "")
        exporter.add_compaction_run(
            run,
            scenario_id=scenario_id,
            book=ctx.book,
            chapter_idx=ctx.long_chapter_idx or ctx.chapter_idx,
            model=config.effective_model() or config.real_llm.model,
            llm_mode=config.llm.mode,
            usage_source=audit_usage_source(config),
            tokens=tokens_by_trace.get(trace_id, {}),
            latency_ms=latency_by_trace.get(trace_id),
        )

    counts = exporter.export()
    ctx.extras["audit_export_counts"] = counts

    async with TargetClient(
        config.target.base_url,
        ctx.run_manager,
        scenario_id,
        "export_agent_audit_compaction",
        context=ctx,
    ) as audit_client:
        agent_counts = await export_agent_audit_artifacts(
            ctx,
            audit_client,
            scenario_id=scenario_id,
            step_id="export_agent_audit_compaction",
        )
        ctx.extras["audit_export_counts"].update(agent_counts)


async def export_s4_compaction_audit(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "export_audit",
) -> None:
    """Export S4 compaction summary, L2 manifest, and agent audit artifacts."""
    exporter: CompactionAuditExporter = ctx.compaction_audit_exporter
    audit_meta = audit_export_metadata(ctx.config)
    model = str(audit_meta["model"] or "")
    compaction_chapter_idx = int(
        ctx.compaction_chapter_idx or ctx.chapter_idx or 0
    )
    compaction_runs = ctx.compaction_agent_runs or []

    for run in compaction_runs:
        exporter.add_compaction_run(
            run,
            scenario_id=scenario_id,
            book=ctx.book,
            chapter_idx=compaction_chapter_idx,
            model=model,
            llm_mode=str(audit_meta["llm_mode"]),
            usage_source=str(audit_meta["usage_source"]),
        )

    counts = exporter.export()
    ctx.extras["audit_export_counts"] = counts

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        "export_agent_audit",
        context=ctx,
    ) as audit_client:
        agent_counts = await export_agent_audit_artifacts(
            ctx,
            audit_client,
            scenario_id=scenario_id,
            step_id="export_agent_audit",
        )
        ctx.extras["audit_export_counts"].update(agent_counts)

        all_agent_runs = await fetch_verify_agent_runs(
            audit_client,
            ctx.run_manager.run_id,
        )
        chapter_agent_runs = filter_agent_runs_for_chapter(
            all_agent_runs,
            compaction_chapter_idx,
        )
        for run in chapter_agent_runs:
            interaction = run.get("interaction") or run
            invocation_id = run.get("invocation_id") or interaction.get("invocation_id")
            if not invocation_id:
                continue
            exporter.add_prompt_manifest_entry(
                invocation_id=str(invocation_id),
                agent=str(run.get("agent") or interaction.get("agent") or ""),
                scenario_id=scenario_id,
                step_id="export_agent_audit",
                prompt_path=f"audit/prompts/{invocation_id}.prompt.md",
                context_hash=str(interaction.get("context_hash") or ""),
                token_estimate=(interaction.get("injected_context") or {}).get(
                    "total_input_token_estimate"
                ),
            )
        ctx.extras["audit_export_counts"].update(exporter.export())

async def export_a3_comment_audit(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "export_comment_audit",
) -> None:
    """Export comment audit samples from the A3 compaction reading flow."""
    config: VerifyConfig = ctx.config
    exporter: CommentAuditExporter = ctx.comment_audit_exporter
    chapter_idx = int(ctx.long_chapter_idx or ctx.chapter_idx or 0)
    assert ctx.book_id is not None

    async with TargetClient(
        config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.list_comments(ctx.book_id, chapter_idx)
        validate_comments_response(body, rec)
        validate_no_span_in_comments(body, rec)
        comments = body.get("items") or []
        jobs = await fetch_verify_jobs(
            client,
            ctx.book_id,
            chapter_idx,
            scenario_id=scenario_id,
            step_id=step_id,
            run_id=ctx.run_manager.run_id,
        )
        trace_ids = unique_trace_ids(comments, jobs)
        (
            tokens_by_trace,
            latency_by_trace,
            trace_meta_by_trace_id,
        ) = await collect_usage_by_trace(client, trace_ids)
    paragraphs = await load_chapter_paragraphs(ctx, ctx.book_id, chapter_idx)
    exporter.add_comments_from_window(
        comments,
        scenario_id=scenario_id,
        book=ctx.book,
        chapter_idx=chapter_idx,
        window=None,
        paragraphs=paragraphs,
        model=config.effective_model() or config.real_llm.model,
        llm_mode=config.llm.mode,
        stub_profile=audit_stub_profile(config),
        usage_source=audit_usage_source(config),
        latency_by_trace=latency_by_trace,
        tokens_by_trace=tokens_by_trace,
        trace_meta_by_trace_id=trace_meta_by_trace_id,
    )
    ndjson_count, md_count = exporter.export()
    ctx.extras["audit_export_counts"] = {
        **(ctx.extras.get("audit_export_counts") or {}),
        "comments_ndjson": ndjson_count,
        "comment_markdown": md_count,
    }


async def export_s2_comment_audit(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "export_audit_samples",
) -> None:
    """Export S2 comment audit samples and agent artifacts (V-15)."""
    from .comments import collect_validation_failures

    exporter: CommentAuditExporter = ctx.comment_audit_exporter
    comments = ctx.comments
    window = ctx.completed_window
    assert ctx.chapter_idx is not None
    assert ctx.book is not None

    audit_meta = audit_export_metadata(ctx.config)
    jobs = ctx.verify_jobs or []
    trace_ids = unique_trace_ids(comments, jobs)

    tokens_by_trace: dict[str, dict[str, Any]] = {}
    latency_by_trace: dict[str, float] = {}
    trace_meta_by_trace_id: dict[str, dict[str, Any]] = {}
    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
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
        scenario_id=scenario_id,
        book=ctx.book,
        chapter_idx=ctx.chapter_idx,
        window=window,
        paragraphs=ctx.chapter_paragraphs,
        model=audit_meta["model"] or "",
        llm_mode=audit_meta["llm_mode"],
        stub_profile=audit_meta["stub_profile"],
        usage_source=audit_meta["usage_source"],
        latency_by_trace=latency_by_trace,
        tokens_by_trace=tokens_by_trace,
        trace_meta_by_trace_id=trace_meta_by_trace_id,
    )

    validation_failures = ctx.extras.get("validation_failures") or collect_validation_failures(
        comments, window
    )

    if not comments:
        exporter.record_window_status(
            scenario_id=scenario_id,
            book=ctx.book,
            chapter_idx=ctx.chapter_idx,
            window=window,
            no_call=ctx.extras.get("window_no_call", False),
            validation_failures=validation_failures,
        )
    elif validation_failures:
        exporter.record_window_status(
            scenario_id=scenario_id,
            book=ctx.book,
            chapter_idx=ctx.chapter_idx,
            window=window,
            no_call=False,
            validation_failures=validation_failures,
        )

    ndjson_count, md_count = exporter.export()
    ctx.extras["audit_export_counts"] = {
        "comments_ndjson": ndjson_count,
        "comment_markdown": md_count,
        "no_call_window": ctx.extras.get("window_no_call", False),
    }

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        "export_agent_audit",
        context=ctx,
    ) as audit_client:
        agent_counts = await export_agent_audit_artifacts(
            ctx,
            audit_client,
            scenario_id=scenario_id,
            step_id="export_agent_audit",
        )
        ctx.extras["audit_export_counts"].update(agent_counts)
    ctx.metrics.record(
        "audit.comments_exported",
        ndjson_count,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )
