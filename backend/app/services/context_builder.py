from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from ..config import Settings
from ..repos import chunks as chunk_repo
from ..repos import comments as comment_repo
from ..repos import paragraphs as paragraph_repo
from ..repos import summaries as summary_repo
from .token_estimator import TokenEstimator

logger = logging.getLogger(__name__)


@dataclass
class ContextBuildResult:
    prompt: str
    estimated_tokens: int
    context_hash: str
    prompt_manifest: dict[str, Any]
    live_chunk_ids: list[int] = field(default_factory=list)
    partial_chunk_id: int | None = None
    partial_frontier_paragraph_idx: int | None = None
    summary_id: int | None = None
    compaction_epoch: int = 0
    preflight_triggered: bool = False
    hard_triggered: bool = False
    context_degraded: bool = False
    emergency_overflow_used: bool = False
    token_estimator_info: dict[str, Any] = field(default_factory=dict)


def _render_chunk_block(
    paragraphs: list[dict[str, Any]],
    chunk: dict[str, Any],
    frontier_pidx: int | None = None,
) -> str:
    lines: list[str] = []
    seq = chunk["chunk_seq"]
    start_p = chunk["start_paragraph_idx"]
    end_p = chunk["end_paragraph_idx"]
    is_partial = frontier_pidx is not None and end_p > frontier_pidx

    if is_partial:
        actual_end = min(frontier_pidx, end_p)
        lines.append(
            f"<PARTIAL_CHUNK seq={seq} start_p={start_p} "
            f"end_p={actual_end} frontier_p={frontier_pidx}>"
        )
    else:
        lines.append(f"<CHUNK seq={seq} start_p={start_p} end_p={end_p}>")

    for p in paragraphs:
        pidx = p["paragraph_idx"]
        if is_partial and pidx > frontier_pidx:
            break
        lines.append(f"[p={pidx}] {p['text']}")

    lines.append("</PARTIAL_CHUNK>" if is_partial else "</CHUNK>")
    return "\n".join(lines)


async def _build_summary_section(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    frontier: int,
) -> tuple[dict[str, Any] | None, int | None, str, int, int]:
    summary = await summary_repo.get_latest_summary(
        db, book_id, chapter_idx, frontier_pidx=frontier
    )
    if not summary:
        return None, None, "", 0, 0

    text = summary["summary"]
    tokens = summary.get("token_estimate", 0)
    epoch = summary.get("compaction_epoch", 0)
    return summary, summary["id"], text, tokens, epoch


async def _build_original_block(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    live_start: int,
    frontier: int,
    skip_chunk_ids: set[int] | None = None,
) -> tuple[str, list[int], int | None, int | None, int]:
    live_chunks = await chunk_repo.get_chunks_intersecting(
        db, book_id, chapter_idx, live_start, frontier
    )
    if skip_chunk_ids:
        live_chunks = [c for c in live_chunks if c["id"] not in skip_chunk_ids]

    lines: list[str] = ["<LIVE_ORIGINAL_CHUNKS>"]
    live_chunk_ids: list[int] = []
    partial_chunk_id = None
    partial_frontier_pidx = None
    original_tokens = 0

    for chunk in live_chunks:
        chunk_id = chunk["id"]
        live_chunk_ids.append(chunk_id)
        chunk_start = max(chunk["start_paragraph_idx"], live_start)
        chunk_end = chunk["end_paragraph_idx"]
        is_partial = chunk_end > frontier and chunk_start <= frontier
        fetch_end = frontier if is_partial else chunk_end

        paragraphs = await paragraph_repo.get_paragraphs_range(
            db, book_id, chapter_idx, chunk_start, fetch_end
        )
        block = _render_chunk_block(
            paragraphs, chunk, frontier_pidx=frontier if is_partial else None
        )
        lines.append(block)

        if is_partial:
            partial_chunk_id = chunk_id
            partial_frontier_pidx = frontier

        if is_partial:
            original_tokens += _estimate_text_tokens(
                "\n".join(p["text"] for p in paragraphs)
            )
        else:
            original_tokens += chunk.get("token_estimate", 0)

    lines.append("</LIVE_ORIGINAL_CHUNKS>")
    return (
        "\n".join(lines),
        live_chunk_ids,
        partial_chunk_id,
        partial_frontier_pidx,
        original_tokens,
    )


def _estimate_text_tokens(text: str) -> int:
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return int(cjk * 1.5 + (len(text) - cjk) * 0.25)


def _make_estimator(settings: Settings) -> TokenEstimator:
    return TokenEstimator(settings.token_estimation)


def _get_estimator(
    settings: Settings, shared: TokenEstimator | None = None
) -> TokenEstimator:
    return shared if shared is not None else _make_estimator(settings)


def _build_comments_block(
    comments: list[dict[str, Any]],
    max_tokens: int,
) -> tuple[str, int]:
    if not comments:
        return "", 0
    c_lines = ["<EPHEMERAL_RECENT_COMMENTS>"]
    total_tokens = 0
    for c in comments:
        line = f"[p={c['paragraph_idx']}] ({c['comment_type']}) {c['comment']}"
        line_tokens = _estimate_text_tokens(line)
        if total_tokens + line_tokens > max_tokens:
            break
        c_lines.append(line)
        total_tokens += line_tokens
    c_lines.append("</EPHEMERAL_RECENT_COMMENTS>")
    return "\n".join(c_lines), total_tokens


def _build_task_block(
    frontier: int,
    focus_start: int | None,
    focus_end: int | None,
    target_paragraphs: list[int] | None,
    density_hint: Any,
) -> tuple[str, int]:
    if target_paragraphs is None:
        return "", 0

    t_lines = ["<CURRENT_TASK>"]
    t_lines.append(f"assistant_frontier_paragraph_idx = {frontier}")
    if focus_start is not None:
        t_lines.append(f"focus_start_paragraph_idx = {focus_start}")
    if focus_end is not None:
        t_lines.append(f"focus_end_paragraph_idx = {focus_end}")
    target_str = ", ".join(str(i) for i in sorted(target_paragraphs))
    t_lines.append(f"comment_target_paragraphs = [{target_str}]")
    t_lines.append("")
    t_lines.append("Rules:")
    t_lines.append("- Only emit comments for comment_target_paragraphs.")
    t_lines.append("- Paragraph text is available in LIVE_ORIGINAL_CHUNKS.")
    t_lines.append(
        "- If paragraph text is missing due context degradation, skip that paragraph."
    )
    t_lines.append("</CURRENT_TASK>")

    if density_hint is not None:
        t_lines.append("")
        t_lines.append("comment_density_hint:")
        t_lines.append(
            f"  stat_start_paragraph_idx = {density_hint.stat_start_paragraph_idx}"
        )
        t_lines.append(
            f"  stat_end_paragraph_idx = {density_hint.stat_end_paragraph_idx}"
        )
        t_lines.append(
            f"  stat_target_paragraph_count = "
            f"{density_hint.stat_target_paragraph_count}"
        )
        t_lines.append(f"  active_comment_count = {density_hint.active_comment_count}")
        t_lines.append(f"  soft_min_density = {density_hint.soft_min_density}")
        t_lines.append(f"  current_density = {density_hint.current_density}")
        t_lines.append(
            f"  estimated_missing_comments = {density_hint.estimated_missing_comments}"
        )

    text = "\n".join(t_lines)
    return text, _estimate_text_tokens(text)


async def _apply_overflow_strategy(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    live_start: int,
    frontier: int,
    live_chunk_ids: list[int],
    partial_chunk_id: int | None,
    partial_frontier_pidx: int | None,
    original_block: str,
    original_tokens: int,
    estimated_tokens: int,
    system_tokens: int,
    metadata_tokens: int,
    reserved_tokens: int,
    summary_tokens: int,
    ephemeral_comment_tokens: int,
    task_tokens: int,
    settings: Settings,
    context_degraded: bool,
    overflow_already_used: bool,
    target_paragraphs: list[int] | None = None,
) -> tuple[str, list[int], int | None, int | None, int, int, bool, bool]:
    ctx_cfg = settings.context
    l3_cfg = settings.context_l3
    emergency_used_now = False

    if estimated_tokens <= l3_cfg.compression_trigger_input_tokens:
        return (
            original_block,
            live_chunk_ids,
            partial_chunk_id,
            partial_frontier_pidx,
            original_tokens,
            estimated_tokens,
            context_degraded,
            overflow_already_used,
        )

    newer_summary = await summary_repo.get_latest_summary(
        db, book_id, chapter_idx, frontier_pidx=frontier
    )

    if (
        newer_summary
        and newer_summary.get("compaction_epoch", 0) > 0
        and live_chunk_ids
    ):
        new_live_start = newer_summary["covered_end_paragraph_idx"] + 1
        if new_live_start > live_start:
            (
                original_block,
                live_chunk_ids,
                partial_chunk_id,
                partial_frontier_pidx,
                original_tokens,
            ) = await _build_original_block(
                db, book_id, chapter_idx, new_live_start, frontier
            )
            summary_tokens_new = newer_summary.get("token_estimate", 0)
            estimated_tokens = (
                system_tokens
                + metadata_tokens
                + reserved_tokens
                + summary_tokens_new
                + original_tokens
                + ephemeral_comment_tokens
                + task_tokens
            )

    if estimated_tokens <= l3_cfg.compression_trigger_input_tokens:
        return (
            original_block,
            live_chunk_ids,
            partial_chunk_id,
            partial_frontier_pidx,
            original_tokens,
            estimated_tokens,
            context_degraded,
            overflow_already_used,
        )

    can_emergency = (
        l3_cfg.allow_emergency_overflow_once
        and not overflow_already_used
        and estimated_tokens <= ctx_cfg.emergency_input_cap_tokens
    )
    if can_emergency:
        emergency_used_now = True
        return (
            original_block,
            live_chunk_ids,
            partial_chunk_id,
            partial_frontier_pidx,
            original_tokens,
            estimated_tokens,
            context_degraded,
            True,
        )

    if live_chunk_ids:
        target_set = set(target_paragraphs) if target_paragraphs else set()
        skip_id: int | None = None
        for cid in live_chunk_ids:
            if cid == partial_chunk_id:
                continue
            chunk = await chunk_repo.get_chunk(db, cid)
            if chunk is None:
                continue
            c_start = chunk["start_paragraph_idx"]
            c_end = chunk["end_paragraph_idx"]
            if target_set and target_set & set(range(c_start, c_end + 1)):
                continue
            skip_id = cid
            break

        if skip_id is not None:
            skip_ids = {skip_id}
            live_chunk_ids = [cid for cid in live_chunk_ids if cid != skip_id]
            context_degraded = True
            (
                original_block,
                live_chunk_ids,
                partial_chunk_id,
                partial_frontier_pidx,
                original_tokens,
            ) = await _build_original_block(
                db,
                book_id,
                chapter_idx,
                live_start,
                frontier,
                skip_chunk_ids=skip_ids,
            )
            estimated_tokens = (
                system_tokens
                + metadata_tokens
                + reserved_tokens
                + summary_tokens
                + original_tokens
                + ephemeral_comment_tokens
                + task_tokens
            )
        else:
            context_degraded = True

    return (
        original_block,
        live_chunk_ids,
        partial_chunk_id,
        partial_frontier_pidx,
        original_tokens,
        estimated_tokens,
        context_degraded,
        overflow_already_used or emergency_used_now,
    )


def _compaction_preflight_thresholds(
    settings: Settings,
) -> tuple[int, int, int]:
    l3_cfg = settings.context_l3
    l2_cfg = settings.context_l2
    return (
        l3_cfg.preflight_trigger_input_tokens,
        l2_cfg.max_live_original_tokens,
        l3_cfg.max_completed_l2_chunks_before_compaction,
    )


async def _check_trigger_conditions(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    frontier: int,
    estimated_tokens: int,
    settings: Settings,
) -> tuple[bool, bool]:
    live_original_tokens = await chunk_repo.get_live_original_tokens(
        db, book_id, chapter_idx, frontier
    )
    completed_chunks = await chunk_repo.count_completed_unreclaimed(
        db, book_id, chapter_idx, frontier
    )

    (
        preflight_input_tokens,
        max_live_original_tokens,
        max_completed_before_compaction,
    ) = _compaction_preflight_thresholds(settings)
    preflight_triggered = (
        estimated_tokens > preflight_input_tokens
        or live_original_tokens > max_live_original_tokens
        or completed_chunks >= max_completed_before_compaction
    )
    hard_triggered = (
        estimated_tokens > settings.context_l3.compression_trigger_input_tokens
    )
    return preflight_triggered, hard_triggered


def _assemble_result(
    *,
    chapter_idx: int,
    book_title: str | None,
    chapter_title: str | None,
    summary_text: str,
    original_block: str,
    comment_block: str,
    task_block: str,
    system_tokens: int,
    metadata_tokens: int,
    reserved_tokens: int,
    summary_tokens: int,
    original_tokens: int,
    ephemeral_comment_tokens: int,
    task_tokens: int,
    estimated_tokens: int,
    live_chunk_ids: list[int],
    partial_chunk_id: int | None,
    partial_frontier_pidx: int | None,
    summary_id: int | None,
    compaction_epoch: int,
    preflight_triggered: bool,
    hard_triggered: bool,
    context_degraded: bool,
    overflow_used: bool,
    settings: Settings,
    ctx_cfg: Any,
    estimator: TokenEstimator | None = None,
) -> ContextBuildResult:
    prompt_parts: list[str] = []

    if book_title or chapter_title:
        m_lines = ["<BOOK_AND_CHAPTER_METADATA>"]
        if book_title:
            m_lines.append(f"book_title = {book_title}")
        m_lines.append(f"chapter_idx = {chapter_idx}")
        if chapter_title:
            m_lines.append(f"chapter_title = {chapter_title}")
        m_lines.append("</BOOK_AND_CHAPTER_METADATA>")
        prompt_parts.append("\n".join(m_lines))
        prompt_parts.append("")

    if summary_text:
        prompt_parts.append("<CHAPTER_COMPRESSED_SUMMARY>")
        prompt_parts.append(summary_text)
        prompt_parts.append("</CHAPTER_COMPRESSED_SUMMARY>")
        prompt_parts.append("")

    prompt_parts.append(original_block)
    prompt_parts.append("")

    if comment_block:
        prompt_parts.append(comment_block)
        prompt_parts.append("")

    if task_block:
        prompt_parts.append(task_block)

    full_prompt = "\n".join(prompt_parts)
    ctx_hash = hashlib.sha256(full_prompt.encode("utf-8")).hexdigest()[:16]

    est = _get_estimator(settings, estimator)
    model = settings.llm.model
    raw_total = _estimate_text_tokens(full_prompt)
    safe_total = est.get_safe_estimate(full_prompt, model)

    estimator_info = est.get_calibration_info(model)

    manifest = {
        "components": [
            {"name": "system_policy", "tokens": system_tokens},
            {"name": "metadata", "tokens": metadata_tokens},
            {"name": "reserved", "tokens": reserved_tokens},
            {"name": "chapter_compressed_summary", "tokens": summary_tokens},
            {"name": "live_original_chunks", "tokens": original_tokens},
            {"name": "ephemeral_recent_comments", "tokens": ephemeral_comment_tokens},
            {"name": "ephemeral_recent_chat", "tokens": 0},
            {"name": "current_task", "tokens": task_tokens},
        ],
        "total_estimate": estimated_tokens,
        "safe_total_estimate": safe_total,
        "raw_total_estimate": raw_total,
        "hard_cap": ctx_cfg.emergency_input_cap_tokens,
        "attention_target": ctx_cfg.attention_target_input_tokens,
        "live_chunk_ids": live_chunk_ids,
        "summary_id": summary_id,
        "compaction_epoch": compaction_epoch,
        "context_hash": ctx_hash,
        "preflight_triggered": preflight_triggered,
        "hard_triggered": hard_triggered,
        "context_degraded": context_degraded,
        "token_estimator": estimator_info,
    }

    return ContextBuildResult(
        prompt=full_prompt,
        estimated_tokens=estimated_tokens,
        context_hash=ctx_hash,
        prompt_manifest=manifest,
        live_chunk_ids=live_chunk_ids,
        partial_chunk_id=partial_chunk_id,
        partial_frontier_paragraph_idx=partial_frontier_pidx,
        summary_id=summary_id,
        compaction_epoch=compaction_epoch,
        preflight_triggered=preflight_triggered,
        hard_triggered=hard_triggered,
        context_degraded=context_degraded,
        emergency_overflow_used=overflow_used,
        token_estimator_info=estimator_info,
    )


async def build_context(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    chapter_idx: int,
    reading_pidx: int,
    settings: Settings,
    task_type: str = "comment",
    focus_start: int | None = None,
    focus_end: int | None = None,
    target_paragraphs: list[int] | None = None,
    density_hint: Any = None,
    book_title: str | None = None,
    chapter_title: str | None = None,
    overflow_already_used: bool = False,
    token_estimator: TokenEstimator | None = None,
) -> ContextBuildResult:
    ctx_cfg = settings.context
    reader_cfg = settings.reader
    eph_comments_cfg = settings.ephemeral_comments
    est = _get_estimator(settings, token_estimator)

    last_pidx = await paragraph_repo.get_last_paragraph_idx(db, book_id, chapter_idx)
    if last_pidx is None:
        raise ValueError(f"No paragraphs for book={book_id} chapter={chapter_idx}")

    frontier = min(reading_pidx + reader_cfg.lookahead_paragraphs, last_pidx)

    (
        summary_row,
        summary_id,
        summary_text,
        summary_tokens,
        compaction_epoch,
    ) = await _build_summary_section(db, book_id, chapter_idx, frontier)

    live_start = 0
    if summary_row is not None:
        live_start = summary_row["covered_end_paragraph_idx"] + 1

    (
        original_block,
        live_chunk_ids,
        partial_chunk_id,
        partial_frontier_pidx,
        original_tokens,
    ) = await _build_original_block(db, book_id, chapter_idx, live_start, frontier)

    comment_block = ""
    ephemeral_comment_tokens = 0
    if focus_start is not None and focus_end is not None:
        margin = eph_comments_cfg.nearby_paragraph_margin
        c_start = max(0, focus_start - margin)
        c_end = focus_end + margin
        comments, _ = await comment_repo.list_comments(
            db, book_id, chapter_idx, start=c_start, end=c_end, limit=50
        )
        comment_block, ephemeral_comment_tokens = _build_comments_block(
            comments, eph_comments_cfg.max_tokens
        )

    task_block, task_tokens = _build_task_block(
        frontier, focus_start, focus_end, target_paragraphs, density_hint
    )

    system_tokens = 3_000
    metadata_tokens = 800
    reserved_tokens = ctx_cfg.reserved_tokens
    estimated_tokens = (
        system_tokens
        + metadata_tokens
        + reserved_tokens
        + summary_tokens
        + original_tokens
        + ephemeral_comment_tokens
        + task_tokens
    )

    # Apply calibration for budget/trigger decisions
    safe_estimated_tokens = (
        est.get_safe_estimate(original_block, settings.llm.model)
        + system_tokens
        + metadata_tokens
        + reserved_tokens
        + summary_tokens
        + ephemeral_comment_tokens
        + task_tokens
    )

    preflight_triggered, hard_triggered = await _check_trigger_conditions(
        db,
        book_id,
        chapter_idx,
        frontier,
        safe_estimated_tokens,
        settings,
    )

    (
        original_block,
        live_chunk_ids,
        partial_chunk_id,
        partial_frontier_pidx,
        original_tokens,
        estimated_tokens,
        context_degraded,
        overflow_used,
    ) = await _apply_overflow_strategy(
        db,
        book_id,
        chapter_idx,
        live_start,
        frontier,
        live_chunk_ids,
        partial_chunk_id,
        partial_frontier_pidx,
        original_block,
        original_tokens,
        safe_estimated_tokens,
        system_tokens,
        metadata_tokens,
        reserved_tokens,
        summary_tokens,
        ephemeral_comment_tokens,
        task_tokens,
        settings,
        False,
        overflow_already_used,
        target_paragraphs=target_paragraphs,
    )

    return _assemble_result(
        chapter_idx=chapter_idx,
        book_title=book_title,
        chapter_title=chapter_title,
        summary_text=summary_text,
        original_block=original_block,
        comment_block=comment_block,
        task_block=task_block,
        system_tokens=system_tokens,
        metadata_tokens=metadata_tokens,
        reserved_tokens=reserved_tokens,
        summary_tokens=summary_tokens,
        original_tokens=original_tokens,
        ephemeral_comment_tokens=ephemeral_comment_tokens,
        task_tokens=task_tokens,
        estimated_tokens=estimated_tokens,
        live_chunk_ids=live_chunk_ids,
        partial_chunk_id=partial_chunk_id,
        partial_frontier_pidx=partial_frontier_pidx,
        summary_id=summary_id,
        compaction_epoch=compaction_epoch,
        preflight_triggered=preflight_triggered,
        hard_triggered=hard_triggered,
        context_degraded=context_degraded,
        overflow_used=overflow_used,
        settings=settings,
        ctx_cfg=ctx_cfg,
        estimator=est,
    )
