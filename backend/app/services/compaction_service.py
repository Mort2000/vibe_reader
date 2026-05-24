from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import aiosqlite

from ..config import Settings
from ..observability import ensure_trace_id
from ..repos import chunks as chunk_repo
from ..repos import context_state
from ..repos import paragraphs as paragraph_repo
from ..repos import summaries as summary_repo
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
                        source_paragraphs, item,
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
                    source_paragraphs, str(excerpt.get("text") or ""),
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


async def run_compaction_task(
    db: aiosqlite.Connection,
    job_id: int,
    window: dict[str, Any] | None,
    settings: Settings,
) -> dict[str, Any] | None:
    job_row = await _get_job(db, job_id)
    if job_row is None:
        raise ValueError(f"Job {job_id} not found")

    book_id = job_row["book_id"]
    chapter_idx = job_row["chapter_idx"]

    state = await _get_book_state(db, book_id)
    frontier_pidx = state.get(
        "assistant_frontier_paragraph_idx", 0
    )

    source_chunk = await chunk_repo.get_earliest_complete_unreclaimed(
        db, book_id, chapter_idx, frontier_pidx
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
    previous_summary_text = None
    previous_covered_start = source_chunk["start_paragraph_idx"]
    previous_source_chunk_ids: list[int] = []
    previous_source_hash = ""

    if previous_summary_row:
        previous_summary_text = previous_summary_row["summary"]
        previous_covered_start = previous_summary_row[
            "covered_start_paragraph_idx"
        ]
        previous_source_chunk_ids = json.loads(
            previous_summary_row.get("source_chunk_ids_json", "[]")
        )
        previous_source_hash = previous_summary_row.get(
            "source_text_hash", ""
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
    usage = result.usage()

    source_chunk_ids = previous_source_chunk_ids + [source_chunk["id"]]
    combined_hash = previous_source_hash + source_chunk["text_hash"]
    source_hash = hashlib.sha256(combined_hash.encode("utf-8")).hexdigest()[:16]

    anchor_excerpts_data = [
        e.model_dump() for e in output.anchor_excerpts
    ]
    summary_text = output.summary
    token_estimate = len(summary_text) + sum(
        len(e.get("text", "")) + len(e.get("reason", ""))
        for e in anchor_excerpts_data
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

    await db.commit()

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
    )

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
                "output_tokens": (
                    usage.response_tokens or usage.output_tokens
                ),
            },
        },
    )

    source_paragraph_count = (
        source_chunk["end_paragraph_idx"] - source_chunk["start_paragraph_idx"] + 1
    )
    source_token_estimate = int(source_chunk.get("token_estimate") or token_estimate)

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
        "compaction_source": {
            "token_estimate": source_token_estimate,
            "paragraph_count": source_paragraph_count,
            "start_paragraph_idx": source_chunk["start_paragraph_idx"],
            "end_paragraph_idx": source_chunk["end_paragraph_idx"],
        },
    }


async def _get_job(
    db: aiosqlite.Connection, job_id: int
) -> dict[str, Any] | None:
    from ..repos import jobs as job_repo

    return await job_repo.get_job(db, job_id)


async def _get_book_state(
    db: aiosqlite.Connection, book_id: int
) -> dict[str, Any]:
    return await context_state.get_or_create(db, book_id)


def register_with_runner(runner: JobRunner) -> None:
    runner.register_handler("compact_context", run_compaction_task)
