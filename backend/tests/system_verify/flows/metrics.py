"""Verify metrics, trace summaries, and usage aggregation."""

from __future__ import annotations

import logging
from typing import Any

from ..assertions.metrics import (
    assert_a3_compaction_phase_coverage,
    assert_real_llm_budget_within_limits,
)
from ..core.client_factory import TargetClient
from ..core.config import VerifyConfig
from ..core.context import ScenarioContext
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager

from .reading import ReadingTrace

logger = logging.getLogger(__name__)


def _iso_duration_ms(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    from datetime import datetime

    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds() * 1000)


def record_comment_metrics(  # noqa: C901
    metrics: MetricsAggregator,
    trace: ReadingTrace,
    *,
    scenario_id: str,
    step_id: str,
    jobs: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
    window: dict[str, Any] | None = None,
    config: VerifyConfig | None = None,
) -> None:
    # TODO(A2): restore comment.e2e_latency_ms once SSE window.done → comment.created
    # timing is reliable; until then use job_queue_wait_ms, job_run_ms, and
    # comment.agent_run_ms from verify metrics / trace summaries.

    total_progress = trace.progress_update_count
    dedup_hits = trace.progress_dedup_count
    if total_progress > 0:
        metrics.record(
            "window_dedup_hit_rate",
            dedup_hits / total_progress,
            unit="ratio",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    if trace.comment_created_count > 0:
        metrics.record(
            "comment.created.count",
            trace.comment_created_count,
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    if trace.window_failed_count:
        metrics.record(
            "comment.window_failed_count",
            trace.window_failed_count,
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    metrics.record(
        "progress.update.count",
        trace.progress_update_count,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )
    metrics.record(
        "window_resolution_count",
        trace.window_resolution_count,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )
    if trace.stale_job_ignored_count:
        metrics.record(
            "stale_job_ignored_count",
            trace.stale_job_ignored_count,
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    comment_jobs = [
        job for job in (jobs or []) if job.get("job_type") in (None, "comment_window")
    ]
    for job in comment_jobs:
        queue_wait = _iso_duration_ms(job.get("created_at"), job.get("started_at"))
        if queue_wait is not None:
            metrics.record(
                "comment.job_queue_wait_ms",
                queue_wait,
                unit="ms",
                scenario_id=scenario_id,
                step_id=step_id,
                tags={"job_id": job.get("id")},
            )
        run_ms = _iso_duration_ms(job.get("started_at"), job.get("completed_at"))
        if run_ms is not None:
            metrics.record(
                "comment.job_run_ms",
                run_ms,
                unit="ms",
                scenario_id=scenario_id,
                step_id=step_id,
                tags={"job_id": job.get("id")},
            )

    window_metrics = _extract_window_comment_metrics(window, comments or [], config)
    for metric_name, value in window_metrics.items():
        if value is None:
            continue
        unit = "ratio" if metric_name.startswith("comment.density") else "count"
        if metric_name.startswith("tokens_per_comment"):
            unit = "tokens"
        metrics.record(
            metric_name,
            float(value),
            unit=unit,
            scenario_id=scenario_id,
            step_id=step_id,
        )

    if comments:
        token_totals = [
            (comment.get("tokens_in") or 0) + (comment.get("tokens_out") or 0)
            for comment in comments
            if comment.get("tokens_in") is not None
            or comment.get("tokens_out") is not None
        ]
        for total in token_totals:
            metrics.record(
                "tokens_per_comment",
                total,
                unit="tokens",
                scenario_id=scenario_id,
                step_id=step_id,
            )
        if token_totals:
            metrics.record(
                "tokens_per_comment_window",
                sum(token_totals),
                unit="tokens",
                scenario_id=scenario_id,
                step_id=step_id,
            )


def _parse_comment_telemetry(
    window: dict[str, Any] | None,
) -> tuple[int, int, dict[str, int], int | None, int | None, int]:
    validation_failed = 0
    discarded = 0
    discarded_by_reason: dict[str, int] = {}
    tool_call_count = window.get("tool_call_count") if window else None
    candidate_lookup_count = window.get("candidate_lookup_count") if window else None

    telemetry = window.get("comment_telemetry") if window else None
    if window and isinstance(telemetry, dict):
        validation_failed = int(telemetry.get("validation_failed_count") or 0)
        reasons = telemetry.get("discarded_by_reason") or {}
        if isinstance(reasons, dict):
            discarded_by_reason = {str(k): int(v) for k, v in reasons.items()}
        if tool_call_count is None:
            tool_call_count = telemetry.get("tool_call_count")
        if candidate_lookup_count is None:
            candidate_lookup_count = telemetry.get("candidate_lookup_count")
        return (
            validation_failed,
            int(telemetry.get("discarded_count") or 0),
            discarded_by_reason,
            tool_call_count,
            candidate_lookup_count,
            1,
        )

    return (
        validation_failed,
        discarded,
        discarded_by_reason,
        tool_call_count,
        candidate_lookup_count,
        0,
    )


def _extract_window_comment_metrics(
    window: dict[str, Any] | None,
    comments: list[dict[str, Any]],
    config: VerifyConfig | None,
) -> dict[str, float | int | None]:
    if not window and not comments:
        return {}

    valid_count = window.get("comments_ready_count") if window else None
    if valid_count is None:
        valid_count = len(comments)

    (
        validation_failed,
        discarded,
        discarded_by_reason,
        tool_call_count,
        candidate_lookup_count,
        telemetry_available,
    ) = _parse_comment_telemetry(window)

    density_actual = None
    stat_start = None
    stat_end = None
    soft_min = config.comment_density.soft_min if config else None
    stat_window = config.comment_density.stat_window_paragraphs if config else None

    if window:
        stat_start = window.get("density_stat_start_paragraph_idx")
        stat_end = window.get("density_stat_end_paragraph_idx")
        density_actual = window.get("comment_density_actual")
        if density_actual is None and stat_start is not None and stat_end is not None:
            span = max(1, int(stat_end) - int(stat_start) + 1)
            density_actual = float(valid_count or 0) / span
        elif density_actual is None and stat_window:
            stat_end = window.get("assistant_frontier_paragraph_idx") or window.get(
                "end_paragraph_idx"
            )
            if stat_end is not None:
                stat_start = max(0, int(stat_end) - stat_window + 1)
                span = max(1, stat_window)
                density_actual = float(valid_count or 0) / span

    metrics: dict[str, float | int | None] = {
        "comment.telemetry_available": telemetry_available if window else None,
        "comment.valid_count": valid_count,
        "comment.validation_failed_count": validation_failed,
        "comment.discarded_count": discarded,
        "comment.tool_call_count": tool_call_count,
        "comment.candidate_lookup_count": candidate_lookup_count,
        "comment.density.actual": density_actual,
        "comment.density.soft_min": soft_min,
        "comment.density.stat_start_paragraph_idx": stat_start,
        "comment.density.stat_end_paragraph_idx": stat_end,
    }
    for reason, count in discarded_by_reason.items():
        metrics[f"comment.discarded_by_reason.{reason}"] = count
    return metrics


def _ai_agent_span(summary: dict[str, Any]) -> dict[str, Any] | None:
    for span in summary.get("spans") or []:
        name = str(span.get("name") or "")
        if name.startswith("ai.") and name.endswith(".run"):
            return span
    return None


def tokens_from_trace_summary(summary: dict[str, Any]) -> dict[str, Any]:
    span = _ai_agent_span(summary)
    if not span:
        return {}
    tokens = span.get("tokens") or {}
    return {
        key: tokens.get(key)
        for key in ("input", "output", "cached_input")
        if tokens.get(key) is not None
    }


def latency_from_trace_summary(summary: dict[str, Any]) -> float | None:
    span = _ai_agent_span(summary)
    if not span:
        return None
    duration = span.get("duration_ms")
    return float(duration) if duration is not None else None


def trace_meta_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_version": summary.get("prompt_version") or "",
        "context_hash": summary.get("context_hash") or "",
    }


async def fetch_trace_summaries(
    client: TargetClient,
    trace_ids: list[str],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for trace_id in trace_ids:
        if not trace_id or trace_id in summaries:
            continue
        body, rec = await client.verify_trace_summary(trace_id)
        if rec.status_code >= 400:
            logger.warning(
                "fetch_trace_summaries: trace %s returned HTTP %s",
                trace_id,
                rec.status_code,
            )
            continue
        summaries[trace_id] = body
    return summaries


async def collect_usage_by_trace(
    client: TargetClient,
    trace_ids: list[str],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, float],
    dict[str, dict[str, Any]],
]:
    summaries = await fetch_trace_summaries(client, trace_ids)
    tokens_by_trace: dict[str, dict[str, Any]] = {}
    latency_by_trace: dict[str, float] = {}
    trace_meta_by_trace_id: dict[str, dict[str, Any]] = {}
    for trace_id, summary in summaries.items():
        tokens = tokens_from_trace_summary(summary)
        if tokens:
            tokens_by_trace[trace_id] = tokens
        latency = latency_from_trace_summary(summary)
        if latency is not None:
            latency_by_trace[trace_id] = latency
        trace_meta_by_trace_id[trace_id] = trace_meta_from_summary(summary)
    return tokens_by_trace, latency_by_trace, trace_meta_by_trace_id


def unique_trace_ids(*sources: list[dict[str, Any]] | None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for source in sources:
        for item in source or []:
            trace_id = item.get("trace_id") or ""
            if trace_id and trace_id not in seen:
                seen.add(trace_id)
                ordered.append(trace_id)
    return ordered


async def sync_real_llm_tracker_from_verify_metrics(
    client: TargetClient,
    run_manager: RunManager,
    config: VerifyConfig,
    *,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    body, rec = await client.verify_metrics(run_manager.run_id, scenario_id=scenario_id)
    if rec.status_code >= 400:
        logger.warning(
            "verify_metrics unavailable for run_id=%s: HTTP %s",
            run_manager.run_id,
            rec.status_code,
        )
        return {}

    tracker = run_manager.real_llm_tracker
    agent_tokens = (body.get("tokens") or {}).get("ParagraphCommentAgent") or {}
    requests = int(agent_tokens.get("requests") or 0)
    input_total = int(agent_tokens.get("input") or 0)
    output_total = int(agent_tokens.get("output") or 0)
    max_input = int(agent_tokens.get("max_input") or 0)
    max_output = int(agent_tokens.get("max_output") or output_total)

    if requests > 0:
        tracker.call_count = requests
        tracker.input_tokens = input_total
        tracker.output_tokens = output_total
        tracker.max_input_tokens_single = max_input
        tracker.max_output_tokens_single = max_output
        if config.params.budget.enforce:
            tracker._check_per_call_limits(config, max_input, max_output)

    return body


def record_verify_metrics_coverage(
    metrics: MetricsAggregator,
    verify_metrics: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str,
) -> None:
    coverage = verify_metrics.get("comment_coverage") or {}
    if not coverage:
        metrics.record(
            "comment.telemetry_available",
            0,
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
        )
        return

    metrics.record(
        "comment.telemetry_available",
        1,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )
    mapping = {
        "comment.tool_call_count": coverage.get("tool_call_count"),
        "comment.candidate_lookup_count": coverage.get("candidate_lookup_count"),
        "comment.valid_count": coverage.get("valid_count"),
        "comment.validation_failed_count": coverage.get("validation_failed_count"),
        "comment.discarded_count": coverage.get("discarded_count"),
        "comment.density.actual": coverage.get("actual_density"),
        "comment.density.soft_min": coverage.get("soft_min_density"),
        "comment.density.stat_start_paragraph_idx": coverage.get(
            "stat_start_paragraph_idx"
        ),
        "comment.density.stat_end_paragraph_idx": coverage.get(
            "stat_end_paragraph_idx"
        ),
    }
    for metric_name, value in mapping.items():
        if value is None:
            continue
        unit = "ratio" if metric_name.startswith("comment.density") else "count"
        metrics.record(
            metric_name,
            float(value),
            unit=unit,
            scenario_id=scenario_id,
            step_id=step_id,
        )

    for reason, count in (coverage.get("discarded_by_reason") or {}).items():
        metrics.record(
            f"comment.discarded_by_reason.{reason}",
            float(count),
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    agent_latency = (verify_metrics.get("latency") or {}).get("comment.agent_run_ms")
    if isinstance(agent_latency, dict):
        for stat in ("p50", "p90", "p95", "max"):
            value = agent_latency.get(stat)
            if value is not None:
                metrics.record(
                    f"comment.agent_run_ms.{stat}",
                    float(value),
                    unit="ms",
                    scenario_id=scenario_id,
                    step_id=step_id,
                )

    agent_tokens = (verify_metrics.get("tokens") or {}).get(
        "ParagraphCommentAgent"
    ) or {}
    requests = int(agent_tokens.get("requests") or 0)
    total_tokens = int(agent_tokens.get("total") or 0)
    if requests > 0 and total_tokens > 0:
        metrics.record(
            "tokens_per_comment_window",
            total_tokens / requests,
            unit="tokens",
            scenario_id=scenario_id,
            step_id=step_id,
        )
        valid_count = int(coverage.get("valid_count") or 0)
        if valid_count > 0:
            metrics.record(
                "tokens_per_comment",
                total_tokens / valid_count,
                unit="tokens",
                scenario_id=scenario_id,
                step_id=step_id,
            )


async def fetch_verify_metrics_for_budget(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str,
) -> dict[str, Any]:
    """Fetch verify metrics over HTTP and sync the real LLM usage tracker."""
    config = ctx.config
    run_manager = ctx.run_manager
    metrics = ctx.metrics
    verify_metrics: dict[str, Any] = {}
    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        verify_metrics = await sync_real_llm_tracker_from_verify_metrics(
            client,
            run_manager,
            config,
            scenario_id=scenario_id,
        )
        if verify_metrics:
            record_verify_metrics_coverage(
                metrics,
                verify_metrics,
                scenario_id=scenario_id,
                step_id=step_id,
            )
    return verify_metrics


def record_budget_guardrail_metrics(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str,
    chapters_crossed_key: str = "chapters_crossed",
) -> None:
    """Record budget guardrail and reading cross-chapter metrics after assertions."""
    run_manager = ctx.run_manager
    metrics = ctx.metrics
    chapters_crossed = ctx.extras.get(chapters_crossed_key, 0)

    tracker = run_manager.real_llm_tracker
    metrics.record(
        "real_llm.call_count",
        tracker.call_count,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )
    metrics.record(
        "real_llm.cost_guardrail_status",
        1 if tracker.cost_guardrail_status == "enforced" else 0,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
        tags={"status": tracker.cost_guardrail_status},
    )
    metrics.record(
        "reading.chapters_crossed",
        chapters_crossed,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )


async def budget_check_step(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "budget_check",
    chapters_crossed_key: str = "chapters_crossed",
) -> None:
    """Sync verify metrics, enforce budget guardrails, and record LLM usage metrics."""
    await fetch_verify_metrics_for_budget(ctx, scenario_id=scenario_id, step_id=step_id)

    tracker = ctx.run_manager.real_llm_tracker
    assert_real_llm_budget_within_limits(tracker, ctx.config)
    record_budget_guardrail_metrics(
        ctx,
        scenario_id=scenario_id,
        step_id=step_id,
        chapters_crossed_key=chapters_crossed_key,
    )


async def record_s5_chat_metrics(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "record_metrics",
) -> None:
    """Aggregate chat metrics recorded per turn in ``verify_s5_chat_turn``.

    Per-turn ``chat.ttft_ms``, ``chat.total_ms``, and token metrics are emitted
    during each chat step. This final step adds turn count plus percentile
    summaries (p50/p90/max/mean) for report-friendly aggregation.
    """
    metrics = ctx.metrics
    turns = ctx.chat_turns or []
    metrics.record(
        "chat.turn_count",
        float(len(turns)),
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )

    _CHAT_AGG_METRICS: tuple[tuple[str, str], ...] = (
        ("chat.ttft_ms", "ms"),
        ("chat.total_ms", "ms"),
        ("chat.tokens.input", "tokens"),
        ("chat.tokens.output", "tokens"),
    )
    for metric_name, unit in _CHAT_AGG_METRICS:
        agg = metrics.aggregate(metric_name)
        if not agg:
            continue
        for stat in ("mean", "p50", "p90", "max"):
            metrics.record(
                f"{metric_name}.{stat}",
                float(agg[stat]),
                unit=unit,
                scenario_id=scenario_id,
                step_id=step_id,
            )
        ctx.extras.setdefault("chat_metric_aggregates", {})[metric_name] = agg


async def budget_check_a4_step(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "budget_check",
) -> None:
    """Budget check with A4 full-flow phase coverage assertions."""
    from ..assertions.metrics import assert_a4_full_flow_phase_coverage

    await budget_check_a3_step(ctx, scenario_id=scenario_id, step_id=step_id)
    tracker = ctx.run_manager.real_llm_tracker
    assert_a4_full_flow_phase_coverage(tracker, ctx.chat_agent_runs or [])


async def budget_check_a3_step(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "budget_check",
) -> None:
    """Budget check with A3 compaction phase coverage assertions."""
    await budget_check_step(ctx, scenario_id=scenario_id, step_id=step_id)
    tracker = ctx.run_manager.real_llm_tracker
    compaction_runs = ctx.compaction_agent_runs
    assert_a3_compaction_phase_coverage(tracker, compaction_runs)


async def record_s4_context_metrics(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "record_metrics",
) -> None:
    """Record S4 context and compaction metrics from verify metrics."""
    from ..assertions.context import record_context_metrics_from_verify

    trace = ctx.reading_trace
    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.verify_metrics(
            ctx.run_manager.run_id,
            scenario_id=scenario_id,
        )
        if rec.status_code < 400 and body:
            record_context_metrics_from_verify(
                ctx.metrics,
                body,
                scenario_id=scenario_id,
                step_id=step_id,
            )
            record_verify_metrics_coverage(
                ctx.metrics,
                body,
                scenario_id=scenario_id,
                step_id=step_id,
            )
    ctx.metrics.record(
        "context.compaction.done_count",
        float(trace.compaction_done_count),
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )


async def record_s2_comment_metrics(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "record_metrics",
) -> None:
    """Record S2 comment latency, dedup, and verify metrics coverage."""
    from .audit import fetch_verify_jobs

    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None

    jobs: list[dict[str, Any]] = []
    verify_metrics: dict[str, Any] = {}
    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        jobs = await fetch_verify_jobs(
            client,
            ctx.book_id,
            ctx.chapter_idx,
            scenario_id=scenario_id,
            step_id=step_id,
        )
        body, rec = await client.verify_metrics(ctx.run_manager.run_id)
        if rec.status_code < 400:
            verify_metrics = body
    ctx.verify_jobs = jobs

    record_comment_metrics(
        ctx.metrics,
        ctx.reading_trace,
        scenario_id=scenario_id,
        step_id=step_id,
        jobs=jobs,
        comments=ctx.comments,
        window=ctx.completed_window,
        config=ctx.config,
    )
    if verify_metrics:
        record_verify_metrics_coverage(
            ctx.metrics,
            verify_metrics,
            scenario_id=scenario_id,
            step_id=step_id,
        )


async def record_s3_scroll_metrics(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "record_metrics",
) -> None:
    """Record S3 scroll/jump dedup metrics and optional jump failure audit."""
    record_comment_metrics(
        ctx.metrics,
        ctx.reading_trace,
        scenario_id=scenario_id,
        step_id=step_id,
    )

    jump_failure_context = ctx.extras.get("jump_failure_context")
    if jump_failure_context:
        ctx.run_manager.write_ndjson(
            "audit/jump_failure_context.ndjson",
            [jump_failure_context],
        )
