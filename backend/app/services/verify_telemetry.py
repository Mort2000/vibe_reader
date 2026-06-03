from __future__ import annotations

import json
import pathlib
from typing import Any

import aiosqlite

from ..repos import verify_telemetry as telemetry_repo

COMMENT_AGENT_NAME = "ParagraphCommentAgent"
PROMPT_VERSION = "paragraph_comment_v1"
COMPACTION_PROMPT_VERSION = "chapter_compaction_v1"


def _load_interaction_for_run(
    run: dict[str, Any],
    *,
    data_dir: pathlib.Path,
) -> dict[str, Any] | None:
    from .agent_audit_store import load_interaction_packet

    interaction_path = run.get("interaction_path") or ""
    if interaction_path:
        loaded = load_interaction_packet(data_dir, interaction_path)
        if loaded is not None:
            return loaded

    # Legacy fallback for rows written before file storage.
    raw = run.get("interaction_json") or ""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


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
        invocation_id=payload.get("invocation_id") or "",
        interaction_path=payload.get("interaction_path") or "",
    )


async def list_agent_run_records(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    scenario_id: str | None = None,
    include_interaction: bool = True,
    data_dir: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    runs = await telemetry_repo.list_agent_runs(
        db, verify_run_id=run_id, verify_scenario_id=scenario_id
    )
    items: list[dict[str, Any]] = []
    for run in runs:
        item = {
            "trace_id": run.get("trace_id"),
            "invocation_id": run.get("invocation_id") or "",
            "interaction_path": run.get("interaction_path") or "",
            "verify_run_id": run.get("verify_run_id") or "",
            "verify_scenario_id": run.get("verify_scenario_id") or "",
            "verify_step_id": run.get("verify_step_id") or "",
            "job_id": run.get("job_id"),
            "window_id": run.get("window_id"),
            "book_id": run.get("book_id"),
            "chapter_idx": run.get("chapter_idx"),
            "agent_name": run.get("agent_name"),
            "duration_ms": run.get("duration_ms"),
            "input_tokens": run.get("input_tokens"),
            "output_tokens": run.get("output_tokens"),
            "cached_input_tokens": run.get("cached_input_tokens"),
            "no_call": bool(run.get("no_call")),
            "tool_call_count": run.get("tool_call_count"),
            "prompt_version": run.get("prompt_version") or "",
            "context_hash": run.get("context_hash") or "",
            "created_at": run.get("created_at"),
        }
        if include_interaction and data_dir is not None:
            item["interaction"] = _load_interaction_for_run(run, data_dir=data_dir)
        items.append(item)
    return items
