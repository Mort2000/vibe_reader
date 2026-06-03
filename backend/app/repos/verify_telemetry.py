from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def record_agent_run(
    db: aiosqlite.Connection,
    *,
    trace_id: str,
    request_id: str,
    verify_run_id: str,
    verify_scenario_id: str,
    verify_step_id: str,
    job_id: int | None,
    window_id: int | None,
    book_id: int | None,
    chapter_idx: int | None,
    agent_name: str,
    duration_ms: float,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
    no_call: bool,
    tool_call_count: int,
    valid_count: int,
    validation_failed_count: int,
    discarded_count: int,
    discarded_by_reason: dict[str, int],
    candidate_lookup_count: int | None,
    prompt_version: str,
    context_hash: str,
    comment_density_actual: float | None,
    comment_density_soft_min: float | None,
    density_stat_start: int | None,
    density_stat_end: int | None,
    status: str = "ok",
    error: str | None = None,
    invocation_id: str = "",
    interaction_path: str = "",
) -> None:
    await db.execute(
        """INSERT INTO verify_agent_runs (
               trace_id, request_id, verify_run_id, verify_scenario_id, verify_step_id,
               job_id, window_id, book_id, chapter_idx, agent_name, duration_ms,
               input_tokens, output_tokens, cached_input_tokens, no_call,
               tool_call_count, valid_count, validation_failed_count, discarded_count,
               discarded_by_reason_json, candidate_lookup_count, prompt_version,
               context_hash, comment_density_actual, comment_density_soft_min,
               density_stat_start, density_stat_end, status, error,
               invocation_id, interaction_path, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(trace_id) DO UPDATE SET
               request_id = excluded.request_id,
               verify_run_id = excluded.verify_run_id,
               verify_scenario_id = excluded.verify_scenario_id,
               verify_step_id = excluded.verify_step_id,
               job_id = excluded.job_id,
               window_id = excluded.window_id,
               book_id = excluded.book_id,
               chapter_idx = excluded.chapter_idx,
               agent_name = excluded.agent_name,
               duration_ms = excluded.duration_ms,
               input_tokens = excluded.input_tokens,
               output_tokens = excluded.output_tokens,
               cached_input_tokens = excluded.cached_input_tokens,
               no_call = excluded.no_call,
               tool_call_count = excluded.tool_call_count,
               valid_count = excluded.valid_count,
               validation_failed_count = excluded.validation_failed_count,
               discarded_count = excluded.discarded_count,
               discarded_by_reason_json = excluded.discarded_by_reason_json,
               candidate_lookup_count = excluded.candidate_lookup_count,
               prompt_version = excluded.prompt_version,
               context_hash = excluded.context_hash,
               comment_density_actual = excluded.comment_density_actual,
               comment_density_soft_min = excluded.comment_density_soft_min,
               density_stat_start = excluded.density_stat_start,
               density_stat_end = excluded.density_stat_end,
               status = excluded.status,
               error = excluded.error,
               invocation_id = excluded.invocation_id,
               interaction_path = excluded.interaction_path,
               created_at = excluded.created_at""",
        (
            trace_id,
            request_id,
            verify_run_id,
            verify_scenario_id,
            verify_step_id,
            job_id,
            window_id,
            book_id,
            chapter_idx,
            agent_name,
            duration_ms,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            1 if no_call else 0,
            tool_call_count,
            valid_count,
            validation_failed_count,
            discarded_count,
            json.dumps(discarded_by_reason, ensure_ascii=False),
            candidate_lookup_count,
            prompt_version,
            context_hash,
            comment_density_actual,
            comment_density_soft_min,
            density_stat_start,
            density_stat_end,
            status,
            error,
            invocation_id,
            interaction_path,
            _now(),
        ),
    )
    await db.commit()


async def list_agent_runs(
    db: aiosqlite.Connection,
    *,
    verify_run_id: str,
    verify_scenario_id: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["verify_run_id = ?"]
    params: list[Any] = [verify_run_id]
    if verify_scenario_id:
        conditions.append("verify_scenario_id = ?")
        params.append(verify_scenario_id)

    where = " AND ".join(conditions)
    cur = await db.execute(
        f"SELECT * FROM verify_agent_runs WHERE {where} ORDER BY created_at ASC",
        params,
    )
    rows = await cur.fetchall()
    return [dict(row) for row in rows]
