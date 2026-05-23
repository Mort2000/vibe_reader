from __future__ import annotations

import json
from typing import Any

import aiosqlite

from ..repos import verify_telemetry as telemetry_repo

COMMENT_AGENT_NAME = "ParagraphCommentAgent"
PROMPT_VERSION = "paragraph_comment_v1"


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; pct=0 returns the minimum value."""
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def _latency_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "p50": None, "p90": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "p50": _percentile(values, 50),
        "p90": _percentile(values, 90),
        "p95": _percentile(values, 95),
        "max": round(max(values), 1),
    }


async def persist_agent_run(
    db: aiosqlite.Connection,
    *,
    trace_id: str,
    job_id: int,
    book_id: int,
    chapter_idx: int,
    window_id: int | None,
    payload: dict[str, Any],
) -> None:
    from ..observability import (
        get_request_id,
        get_verify_run_id,
        get_verify_scenario_id,
        get_verify_step_id,
    )

    await telemetry_repo.record_agent_run(
        db,
        trace_id=trace_id,
        request_id=get_request_id(),
        verify_run_id=get_verify_run_id(),
        verify_scenario_id=get_verify_scenario_id(),
        verify_step_id=get_verify_step_id(),
        job_id=job_id,
        window_id=window_id,
        book_id=book_id,
        chapter_idx=chapter_idx,
        agent_name=payload.get("agent_name") or COMMENT_AGENT_NAME,
        duration_ms=float(payload.get("duration_ms") or 0),
        input_tokens=payload.get("input_tokens"),
        output_tokens=payload.get("output_tokens"),
        cached_input_tokens=payload.get("cached_input_tokens"),
        no_call=bool(payload.get("no_call")),
        tool_call_count=int(payload.get("tool_call_count") or 0),
        valid_count=int(payload.get("valid_count") or 0),
        validation_failed_count=int(payload.get("validation_failed_count") or 0),
        discarded_count=int(payload.get("discarded_count") or 0),
        discarded_by_reason=payload.get("discarded_by_reason") or {},
        candidate_lookup_count=payload.get("candidate_lookup_count"),
        prompt_version=payload.get("prompt_version") or PROMPT_VERSION,
        context_hash=payload.get("context_hash") or "",
        comment_density_actual=payload.get("comment_density_actual"),
        comment_density_soft_min=payload.get("comment_density_soft_min"),
        density_stat_start=payload.get("density_stat_start"),
        density_stat_end=payload.get("density_stat_end"),
    )


async def get_trace_summary(
    db: aiosqlite.Connection, trace_id: str
) -> dict[str, Any] | None:
    run = await telemetry_repo.get_agent_run(db, trace_id)
    if run is None:
        return None

    tokens: dict[str, int | None] = {
        "input": run.get("input_tokens"),
        "output": run.get("output_tokens"),
        "cached_input": run.get("cached_input_tokens"),
    }

    spans: list[dict[str, Any]] = [
        {
            "name": f"ai.{run['agent_name']}.run",
            "duration_ms": round(float(run.get("duration_ms") or 0), 1),
            "status": run.get("status") or "ok",
            "tokens": tokens,
        }
    ]

    errors: list[dict[str, Any]] = []
    if run.get("error"):
        errors.append({"message": run["error"], "span": spans[0]["name"]})

    root_span = "job comment_window"
    if run.get("verify_step_id"):
        root_span = f"verify {run['verify_step_id']}"

    return {
        "trace_id": trace_id,
        "request_id": run.get("request_id") or "",
        "verify_run_id": run.get("verify_run_id") or "",
        "root_span": root_span,
        "spans": spans,
        "errors": errors,
        "prompt_version": run.get("prompt_version") or "",
        "context_hash": run.get("context_hash") or "",
    }


async def aggregate_metrics(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    runs = await telemetry_repo.list_agent_runs(
        db, verify_run_id=run_id, verify_scenario_id=scenario_id
    )

    comment_runs = [
        run for run in runs if run.get("agent_name") == COMMENT_AGENT_NAME
    ]

    input_total = 0
    output_total = 0
    max_input = 0
    max_output = 0
    durations: list[float] = []
    tool_call_count = 0
    valid_count = 0
    validation_failed_count = 0
    discarded_count = 0
    discarded_by_reason: dict[str, int] = {}
    candidate_lookup_count = 0
    no_call_window_count = 0
    density_values: list[float] = []
    soft_min_values: list[float] = []
    stat_start_values: list[int] = []
    stat_end_values: list[int] = []

    for run in comment_runs:
        input_tokens = int(run.get("input_tokens") or 0)
        output_tokens = int(run.get("output_tokens") or 0)
        input_total += input_tokens
        output_total += output_tokens
        max_input = max(max_input, input_tokens)
        max_output = max(max_output, output_tokens)
        durations.append(float(run.get("duration_ms") or 0))
        tool_call_count += int(run.get("tool_call_count") or 0)
        valid_count += int(run.get("valid_count") or 0)
        validation_failed_count += int(run.get("validation_failed_count") or 0)
        discarded_count += int(run.get("discarded_count") or 0)
        candidate_lookup_count += int(run.get("candidate_lookup_count") or 0)
        if int(run.get("no_call") or 0):
            no_call_window_count += 1

        reasons_raw = run.get("discarded_by_reason_json") or "{}"
        try:
            reasons = json.loads(reasons_raw)
        except json.JSONDecodeError:
            reasons = {}
        if isinstance(reasons, dict):
            for reason, count in reasons.items():
                discarded_by_reason[str(reason)] = discarded_by_reason.get(
                    str(reason), 0
                ) + int(count)

        if run.get("comment_density_actual") is not None:
            density_values.append(float(run["comment_density_actual"]))
        if run.get("comment_density_soft_min") is not None:
            soft_min_values.append(float(run["comment_density_soft_min"]))
        if run.get("density_stat_start") is not None:
            stat_start_values.append(int(run["density_stat_start"]))
        if run.get("density_stat_end") is not None:
            stat_end_values.append(int(run["density_stat_end"]))

    tokens_payload: dict[str, Any] = {}
    if comment_runs:
        tokens_payload[COMMENT_AGENT_NAME] = {
            "requests": len(comment_runs),
            "input": input_total,
            "output": output_total,
            "total": input_total + output_total,
            "max_input": max_input,
            "max_output": max_output,
        }

    comment_coverage: dict[str, Any] | None = None
    if comment_runs:
        actual_density = (
            round(sum(density_values) / len(density_values), 4)
            if density_values
            else None
        )
        comment_coverage = {
            "soft_min_density": soft_min_values[0] if soft_min_values else None,
            "actual_density": actual_density,
            "stat_window_paragraphs": None,
            "stat_start_paragraph_idx": stat_start_values[0]
            if stat_start_values
            else None,
            "stat_end_paragraph_idx": stat_end_values[-1] if stat_end_values else None,
            "tool_call_count": tool_call_count,
            "valid_count": valid_count,
            "validation_failed_count": validation_failed_count,
            "discarded_count": discarded_count,
            "discarded_by_reason": discarded_by_reason,
            "candidate_lookup_count": candidate_lookup_count,
            "no_call_window_count": no_call_window_count,
        }

    return {
        "run_id": run_id,
        "latency": {
            "comment.agent_run_ms": _latency_summary(durations),
        },
        "tokens": tokens_payload,
        "cache": {
            "llm_prompt_cache_hit_rate": None,
            "llm_prompt_cache_hit_rate_available": False,
            "context_cache_hit_rate": None,
            "window_dedup_hit_rate": None,
            "comment_reuse_hit_rate": None,
        },
        "comment_coverage": comment_coverage,
    }
