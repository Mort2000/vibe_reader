from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import aiosqlite

from ..config import Settings
from ..observability import (
    ensure_trace_id,
    get_verify_run_id,
    get_verify_scenario_id,
    get_verify_step_id,
)
from ..repos import chunks as chunk_repo
from ..repos import context_state
from ..repos import paragraphs as paragraph_repo
from ..repos import summaries as summary_repo
from .agent_audit import build_compaction_interaction_packet, make_invocation_id
from .agent_audit_store import persist_interaction_packet
from .agent_base import (
    ChapterCompressedSummaryOutput,
    CompactionDeps,
    get_compaction_agent,
)
from .context_builder import _estimate_text_tokens
from .job_runner import JobRunner

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    return _estimate_text_tokens(text)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    if _estimate_tokens(text) <= max_tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _estimate_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def _find_paragraph_idx_for_text(
    paragraphs: list[dict[str, Any]],
    text: str,
) -> int | None:
    needle = text.strip()
    if not needle:
        return None
    for paragraph in paragraphs:
        body = paragraph.get("text") or ""
        if needle in body or body in needle:
            return int(paragraph["paragraph_idx"])
    return None


def _normalize_anchor_excerpts(
    excerpts: list[Any],
    *,
    chapter_idx: int,
    source_paragraphs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in excerpts:
        if isinstance(item, str):
            normalized.append(
                {
                    "chapter_idx": chapter_idx,
                    "paragraph_idx": _find_paragraph_idx_for_text(
                        source_paragraphs,
                        item,
                    ),
                    "text": item,
                    "reason": "anchor",
                }
            )
        elif isinstance(item, dict):
            excerpt = dict(item)
            excerpt.setdefault("chapter_idx", chapter_idx)
            if excerpt.get("paragraph_idx") is None:
                excerpt["paragraph_idx"] = _find_paragraph_idx_for_text(
                    source_paragraphs,
                    str(excerpt.get("text") or ""),
                )
            normalized.append(excerpt)
    return normalized


def _trim_anchor_excerpts(
    excerpts: list[dict[str, Any]],
    *,
    max_count: int,
    max_tokens: int,
) -> list[dict[str, Any]]:
    trimmed: list[dict[str, Any]] = []
    for excerpt in excerpts[:max_count]:
        text = str(excerpt.get("text") or "")
        reason = str(excerpt.get("reason") or "")
        if _estimate_tokens(text) > max_tokens:
            text = _truncate_to_tokens(text, max_tokens)
        if _estimate_tokens(text) + _estimate_tokens(reason) > max_tokens:
            continue
        trimmed.append({**excerpt, "text": text})
    return trimmed


def _build_compaction_prompt(
    previous_summary: str | None,
    chunk_text: str,
) -> str:
    lines: list[str] = []
    if previous_summary:
        lines.append("<PREVIOUS_CHAPTER_SUMMARY>")
        lines.append(previous_summary)
        lines.append("</PREVIOUS_CHAPTER_SUMMARY>")
        lines.append("")

    lines.append("<SOURCE_ORIGINAL_CHUNK>")
    lines.append(chunk_text)
    lines.append("</SOURCE_ORIGINAL_CHUNK>")

    return "\n".join(lines)


async def _run_compaction_llm(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    chapter_idx: int,
    job_id: int,
    source_chunk: dict[str, Any],
    previous_summary_row: dict[str, Any] | None,
    settings: Settings,
) -> tuple[Any, CompactionDeps, str, float, str, Any]:
    previous_summary_text = (
        previous_summary_row["summary"] if previous_summary_row else None
    )

    paragraphs = await paragraph_repo.get_paragraphs_range(
        db,
        book_id,
        chapter_idx,
        source_chunk["start_paragraph_idx"],
        source_chunk["end_paragraph_idx"],
    )
    chunk_text = "\n".join(p["text"] for p in paragraphs)

    prompt = _build_compaction_prompt(previous_summary_text, chunk_text)

    deps = CompactionDeps(
        previous_summary=previous_summary_text,
        chunk_text=chunk_text,
    )

    agent = get_compaction_agent(settings)
    trace_id = ensure_trace_id()

    t0 = time.monotonic()
    result = await agent.run(
        prompt,
        deps=deps,
        metadata={
            "book_id": book_id,
            "chapter_idx": chapter_idx,
            "job_id": job_id,
            "trace_id": trace_id,
        },
    )
    latency_ms = (time.monotonic() - t0) * 1000

    if not deps.raw_output:
        raise ValueError("Compaction agent did not emit chapter compressed summary")

    raw_output = dict(deps.raw_output)
    excerpts = raw_output.get("anchor_excerpts") or []
    normalized_excerpts = _normalize_anchor_excerpts(
        excerpts,
        chapter_idx=chapter_idx,
        source_paragraphs=paragraphs,
    )
    normalized_excerpts = _trim_anchor_excerpts(
        normalized_excerpts,
        max_count=settings.context.max_anchor_excerpts,
        max_tokens=settings.context.max_anchor_excerpt_tokens,
    )
    raw_output["anchor_excerpts"] = normalized_excerpts

    output = ChapterCompressedSummaryOutput.model_validate(raw_output)

    return result, deps, prompt, latency_ms, trace_id, output


async def run_compaction_task(
    db: aiosqlite.Connection,
    job_id: int,
    window: dict[str, Any] | None,
    settings: Settings,
    token_estimator: Any = None,
) -> dict[str, Any] | None:
    job_row = await _get_job(db, job_id)
    if job_row is None:
        raise ValueError(f"Job {job_id} not found")

    book_id = job_row["book_id"]
    chapter_idx = job_row["chapter_idx"]

    frontier_pidx = await _compaction_frontier_paragraph_idx(db, book_id, chapter_idx)

    source_chunk = await chunk_repo.select_eligible_compaction_source(
        db,
        book_id,
        chapter_idx,
        frontier_pidx,
        min_live_chunks_after_compaction=settings.context_l2.min_live_chunks_after_compaction,
    )
    if source_chunk is None:
        logger.info(
            "compaction.no_chunk",
            extra={
                "event": "compaction.no_chunk",
                "fields": {"job_id": job_id, "book_id": book_id},
            },
        )
        return None

    previous_summary_row = await summary_repo.get_latest_summary(
        db, book_id, chapter_idx
    )
    previous_covered_start = source_chunk["start_paragraph_idx"]
    previous_source_chunk_ids: list[int] = []
    previous_source_hash = ""

    if previous_summary_row:
        previous_covered_start = previous_summary_row["covered_start_paragraph_idx"]
        previous_source_chunk_ids = json.loads(
            previous_summary_row.get("source_chunk_ids_json", "[]")
        )
        previous_source_hash = previous_summary_row.get("source_text_hash", "")

    result, deps, prompt, latency_ms, trace_id, output = await _run_compaction_llm(
        db,
        book_id=book_id,
        chapter_idx=chapter_idx,
        job_id=job_id,
        source_chunk=source_chunk,
        previous_summary_row=previous_summary_row,
        settings=settings,
    )

    usage = result.usage()

    source_chunk_ids = previous_source_chunk_ids + [source_chunk["id"]]
    combined_hash = previous_source_hash + source_chunk["text_hash"]
    source_hash = hashlib.sha256(combined_hash.encode("utf-8")).hexdigest()[:16]

    anchor_excerpts_data = [e.model_dump() for e in output.anchor_excerpts]
    summary_text = output.summary
    token_estimate = len(summary_text) + sum(
        len(e.get("text", "")) + len(e.get("reason", "")) for e in anchor_excerpts_data
    )

    compaction_epoch = (
        (previous_summary_row.get("compaction_epoch", 0) + 1)
        if previous_summary_row
        else 1
    )

    summary_row = await summary_repo.create_summary(
        db,
        book_id=book_id,
        chapter_idx=chapter_idx,
        covered_start_paragraph_idx=previous_covered_start,
        covered_end_paragraph_idx=source_chunk["end_paragraph_idx"],
        source_chunk_ids=source_chunk_ids,
        source_text_hash=source_hash,
        summary=summary_text,
        anchor_excerpts=anchor_excerpts_data,
        token_estimate=token_estimate,
        context_version=1,
        compaction_epoch=compaction_epoch,
        auto_commit=False,
    )

    await chunk_repo.mark_reclaimed(
        db, source_chunk["id"], summary_row["id"], auto_commit=False
    )

    live_chunks = await chunk_repo.list_chunks(
        db, book_id, chapter_idx, status="active"
    )
    live_chunk_ids = [c["id"] for c in live_chunks]

    await context_state.update_state(
        db,
        book_id,
        latest_summary_id=summary_row["id"],
        compaction_epoch=compaction_epoch,
        live_l2_chunk_ids=live_chunk_ids,
        auto_commit=False,
    )

    await db.commit()

    transaction_committed = True

    logger.info(
        "compaction.completed",
        extra={
            "event": "compaction.completed",
            "fields": {
                "job_id": job_id,
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "source_chunk_id": source_chunk["id"],
                "source_chunk_range": (
                    f"{source_chunk['start_paragraph_idx']}-"
                    f"{source_chunk['end_paragraph_idx']}"
                ),
                "summary_id": summary_row["id"],
                "summary_tokens": token_estimate,
                "compaction_epoch": compaction_epoch,
                "latency_ms": round(latency_ms, 1),
                "input_tokens": usage.request_tokens or usage.input_tokens,
                "output_tokens": (usage.response_tokens or usage.output_tokens),
            },
        },
    )

    source_paragraph_count = (
        source_chunk["end_paragraph_idx"] - source_chunk["start_paragraph_idx"] + 1
    )
    source_token_estimate = int(source_chunk.get("token_estimate") or token_estimate)

    invocation_id = make_invocation_id(
        "ContextCompactionAgent",
        get_verify_scenario_id(),
        job_id,
    )
    interaction_path = ""
    if settings.verify_mode:
        usage_input = (
            usage.request_tokens
            if usage.request_tokens is not None
            else usage.input_tokens
        )
        usage_output = (
            usage.response_tokens
            if usage.response_tokens is not None
            else usage.output_tokens
        )
        interaction = build_compaction_interaction_packet(
            invocation_id=invocation_id,
            trace_id=trace_id,
            verify_run_id=get_verify_run_id(),
            verify_scenario_id=get_verify_scenario_id(),
            verify_step_id=get_verify_step_id(),
            job_id=job_id,
            book_id=book_id,
            chapter_idx=chapter_idx,
            source_chunk=source_chunk,
            previous_summary_row=previous_summary_row,
            next_summary_row=summary_row,
            prompt=prompt,
            agent_result=result,
            settings=settings,
            duration_ms=round(latency_ms, 1),
            input_tokens=usage_input,
            output_tokens=usage_output,
            cached_input_tokens=usage.cache_read_tokens or None,
            transaction_committed=transaction_committed,
        )
        interaction_path = persist_interaction_packet(
            settings.data_dir,
            verify_run_id=get_verify_run_id(),
            invocation_id=invocation_id,
            packet=interaction,
        )

    return {
        "agent_name": "ContextCompactionAgent",
        "duration_ms": round(latency_ms, 1),
        "input_tokens": usage.request_tokens or usage.input_tokens,
        "output_tokens": usage.response_tokens or usage.output_tokens,
        "cached_input_tokens": usage.cache_read_tokens or None,
        "source_chunk_id": source_chunk["id"],
        "reclaimed_chunk_id": source_chunk["id"],
        "summary_id": summary_row["id"],
        "compaction_epoch": compaction_epoch,
        "context_hash": source_hash,
        "source_chunk_tokens": source_token_estimate,
        "source_paragraph_count": source_paragraph_count,
        "source_chunk_hash": source_chunk.get("text_hash", ""),
        "source_chunk_start": source_chunk["start_paragraph_idx"],
        "source_chunk_end": source_chunk["end_paragraph_idx"],
        "previous_summary_id": previous_summary_row["id"]
        if previous_summary_row
        else None,
        "transaction_committed": transaction_committed,
        "invocation_id": invocation_id,
        "interaction_path": interaction_path,
        "prompt_version": "chapter_compaction_v1",
        "compaction_source": {
            "token_estimate": source_token_estimate,
            "paragraph_count": source_paragraph_count,
            "start_paragraph_idx": source_chunk["start_paragraph_idx"],
            "end_paragraph_idx": source_chunk["end_paragraph_idx"],
        },
    }


async def _get_job(db: aiosqlite.Connection, job_id: int) -> dict[str, Any] | None:
    from ..repos import jobs as job_repo

    return await job_repo.get_job(db, job_id)


async def _get_book_state(db: aiosqlite.Connection, book_id: int) -> dict[str, Any]:
    return await context_state.get_or_create(db, book_id)


async def _compaction_frontier_paragraph_idx(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
) -> int:
    """Frontier used to decide which L2 chunks are complete for *chapter_idx*."""
    from ..repos import chapters as chapter_repo

    state = await _get_book_state(db, book_id)
    active_chapter = state.get("assistant_frontier_chapter_idx")
    if active_chapter == chapter_idx:
        return int(state.get("assistant_frontier_paragraph_idx") or 0)

    chapter = await chapter_repo.get_chapter(db, book_id, chapter_idx)
    if not chapter:
        return 0
    return max(0, int(chapter.get("paragraph_count") or 1) - 1)


async def select_compaction_source_for_chapter(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    settings: Settings,
) -> dict[str, Any] | None:
    """Return the earliest eligible L2 chunk for compaction, if any."""
    frontier_pidx = await _compaction_frontier_paragraph_idx(db, book_id, chapter_idx)
    return await chunk_repo.select_eligible_compaction_source(
        db,
        book_id,
        chapter_idx,
        frontier_pidx,
        min_live_chunks_after_compaction=settings.context_l2.min_live_chunks_after_compaction,
    )


async def maybe_enqueue_compaction(
    db: aiosqlite.Connection,
    job_runner: JobRunner,
    book_id: int,
    chapter_idx: int,
    settings: Settings,
    *,
    preflight_triggered: bool,
) -> bool:
    """Enqueue ``compact_context`` only when preflight fired and a source chunk exists."""
    if not preflight_triggered:
        return False

    source_chunk = await select_compaction_source_for_chapter(
        db,
        book_id,
        chapter_idx,
        settings,
    )
    if source_chunk is None:
        logger.info(
            "compaction.enqueue_skipped",
            extra={
                "event": "compaction.enqueue_skipped",
                "fields": {
                    "book_id": book_id,
                    "chapter_idx": chapter_idx,
                    "reason": "no_eligible_source",
                },
            },
        )
        return False

    await job_runner.submit_job(db, "compact_context", book_id, chapter_idx)
    return True


def register_with_runner(runner: JobRunner) -> None:
    runner.register_handler("compact_context", run_compaction_task)
