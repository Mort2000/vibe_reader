from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from ..config import Settings
from ..domain.budget_policy import (
    TriggerDecision,
    can_use_emergency_overflow,
    evaluate_triggers,
    exceeds_compression_threshold,
)
from ..domain.context_plan import ContextPlan, LiveOriginalChunkSelection
from ..domain.models import ChapterCompressedSummary, OriginalTextChunk
from ..observability import (
    mark_span_error,
    record_context_build_metric,
    set_span_attributes,
    start_observable_span,
)
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
    chunk: OriginalTextChunk,
    frontier_pidx: int | None = None,
) -> str:
    lines: list[str] = []
    seq = chunk.chunk_seq
    start_p = chunk.start_paragraph_idx
    end_p = chunk.end_paragraph_idx
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
) -> tuple[ChapterCompressedSummary | None, int | None, str, int, int]:
    summary = await summary_repo.get_latest_summary(
        db, book_id, chapter_idx, frontier_pidx=frontier
    )
    if not summary:
        return None, None, "", 0, 0

    text = summary.summary
    tokens = summary.token_estimate
    epoch = summary.compaction_epoch
    return summary, summary.id, text, tokens, epoch


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
        live_chunks = [c for c in live_chunks if c.id not in skip_chunk_ids]

    lines: list[str] = ["<LIVE_ORIGINAL_CHUNKS>"]
    live_chunk_ids: list[int] = []
    partial_chunk_id = None
    partial_frontier_pidx = None
    original_tokens = 0

    for chunk in live_chunks:
        chunk_id = chunk.id
        live_chunk_ids.append(chunk_id)
        chunk_start = max(chunk.start_paragraph_idx, live_start)
        chunk_end = chunk.end_paragraph_idx
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
            original_tokens += chunk.token_estimate

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


def _get_estimator(
    settings: Settings, shared: TokenEstimator | None = None
) -> TokenEstimator:
    if shared is not None:
        return shared
    return TokenEstimator(settings.token_estimation)


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


def _paragraphs_to_ranges(paragraphs: list[int]) -> str:
    if not paragraphs:
        return ""
    parts: list[str] = []
    start = prev = paragraphs[0]
    for p in paragraphs[1:]:
        if p == prev + 1:
            prev = p
            continue
        parts.append(f"{start}..={prev}" if start != prev else str(start))
        start = prev = p
    parts.append(f"{start}..={prev}" if start != prev else str(start))
    return ", ".join(parts)


def _build_chat_task_block(reading_pidx: int) -> tuple[str, int]:
    t_lines = ["<CURRENT_TASK>"]
    t_lines.append(f"current_reading_paragraph_idx = {reading_pidx}")
    t_lines.append("mode = chat")
    t_lines.append("</CURRENT_TASK>")
    text = "\n".join(t_lines)
    return text, _estimate_text_tokens(text)


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
    target_str = _paragraphs_to_ranges(sorted(target_paragraphs))
    t_lines.append(f"comment_target_paragraphs = [{target_str}]")
    t_lines.append("")
    t_lines.append("Rules:")
    t_lines.append("- Only emit comments for comment_target_paragraphs.")
    t_lines.append(
        "- When more than one useful comment is needed, call emit_comment multiple "
        "times in the same response."
    )
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


async def _fetch_trigger_inputs(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    frontier: int,
    safe_estimated_tokens: int,
    settings: Settings,
) -> TriggerDecision:
    live_original_tokens = await chunk_repo.get_live_original_tokens(
        db, book_id, chapter_idx, frontier
    )
    completed_chunks = await chunk_repo.count_completed_unreclaimed(
        db, book_id, chapter_idx, frontier
    )
    l3_cfg = settings.context_l3
    l2_cfg = settings.context_l2
    return evaluate_triggers(
        safe_estimated_tokens,
        live_original_tokens,
        completed_chunks,
        preflight_trigger_tokens=l3_cfg.preflight_trigger_input_tokens,
        max_live_original_tokens=l2_cfg.max_live_original_tokens,
        max_completed_before_compaction=l3_cfg.max_completed_l2_chunks_before_compaction,
        min_completed_before_compaction=l3_cfg.min_completed_l2_chunks_before_compaction,
        compression_trigger_tokens=l3_cfg.compression_trigger_input_tokens,
    )


async def _apply_overflow(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    plan: ContextPlan,
    safe_estimated_tokens: int,
    settings: Settings,
    overflow_already_used: bool,
    target_paragraphs: list[int] | None = None,
) -> tuple[ContextPlan, int, bool, bool]:
    """Apply overflow/degradation strategy.

    Returns (plan, estimated_tokens, context_degraded, emergency_overflow_used).
    """
    l3_cfg = settings.context_l3
    ctx_cfg = settings.context

    if not exceeds_compression_threshold(
        safe_estimated_tokens, l3_cfg.compression_trigger_input_tokens
    ):
        return plan, safe_estimated_tokens, False, False

    newer_summary = await summary_repo.get_latest_summary(
        db, book_id, chapter_idx, frontier_pidx=plan.frontier
    )

    if (
        newer_summary
        and newer_summary.compaction_epoch > 0
        and plan.live_chunks.chunk_ids
    ):
        new_live_start = newer_summary.covered_end_paragraph_idx + 1
        if new_live_start > plan.live_start:
            (
                original_block,
                live_chunk_ids,
                partial_chunk_id,
                partial_frontier_pidx,
                original_tokens,
            ) = await _build_original_block(
                db, book_id, chapter_idx, new_live_start, plan.frontier
            )
            plan.live_chunks = LiveOriginalChunkSelection(
                block_text=original_block,
                chunk_ids=live_chunk_ids,
                partial_chunk_id=partial_chunk_id,
                partial_frontier_paragraph_idx=partial_frontier_pidx,
                estimated_tokens=original_tokens,
            )
            plan.live_start = new_live_start
            plan.summary_tokens = newer_summary.token_estimate

            if not exceeds_compression_threshold(
                plan.estimated_tokens, l3_cfg.compression_trigger_input_tokens
            ):
                return plan, plan.estimated_tokens, False, False

    if can_use_emergency_overflow(
        safe_estimated_tokens,
        overflow_already_used,
        allow_emergency_overflow=l3_cfg.allow_emergency_overflow_once,
        emergency_cap_tokens=ctx_cfg.emergency_input_cap_tokens,
        compression_trigger_tokens=l3_cfg.compression_trigger_input_tokens,
    ):
        return plan, safe_estimated_tokens, False, True

    logger.warning(
        "context_builder.overflow_without_live_drop",
        extra={
            "event": "context_builder.overflow_without_live_drop",
            "fields": {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "frontier": plan.frontier,
                "estimated_tokens": safe_estimated_tokens,
            },
        },
    )
    return plan, safe_estimated_tokens, False, False


def render_context(
    plan: ContextPlan,
    *,
    estimated_tokens: int,
    trigger: TriggerDecision,
    context_degraded: bool,
    emergency_overflow_used: bool,
    estimator: TokenEstimator,
    settings: Settings,
    calibration_model: str,
) -> ContextBuildResult:
    """Render a ContextPlan into a prompt string and manifest."""
    prompt_parts: list[str] = []

    if plan.book_title or plan.chapter_title:
        m_lines = ["<BOOK_AND_CHAPTER_METADATA>"]
        if plan.book_title:
            m_lines.append(f"book_title = {plan.book_title}")
        m_lines.append(f"chapter_idx = {plan.chapter_idx}")
        if plan.chapter_title:
            m_lines.append(f"chapter_title = {plan.chapter_title}")
        m_lines.append("</BOOK_AND_CHAPTER_METADATA>")
        prompt_parts.append("\n".join(m_lines))
        prompt_parts.append("")

    if plan.summary_text:
        prompt_parts.append("<CHAPTER_COMPRESSED_SUMMARY>")
        prompt_parts.append(plan.summary_text)
        prompt_parts.append("</CHAPTER_COMPRESSED_SUMMARY>")
        prompt_parts.append("")

    prompt_parts.append(plan.live_chunks.block_text)
    prompt_parts.append("")

    if plan.comments_text:
        prompt_parts.append(plan.comments_text)
        prompt_parts.append("")

    if plan.task_text:
        prompt_parts.append(plan.task_text)

    full_prompt = "\n".join(prompt_parts)
    ctx_hash = hashlib.sha256(full_prompt.encode("utf-8")).hexdigest()[:16]

    raw_total = _estimate_text_tokens(full_prompt)
    safe_total = estimator.get_safe_estimate(full_prompt, calibration_model)
    estimator_info = estimator.get_calibration_info(calibration_model)

    ctx_cfg = settings.context
    manifest = {
        "components": [
            {"name": "system_policy", "tokens": plan.system_tokens},
            {"name": "metadata", "tokens": plan.metadata_tokens},
            {"name": "reserved", "tokens": plan.reserved_tokens},
            {"name": "chapter_compressed_summary", "tokens": plan.summary_tokens},
            {
                "name": "live_original_chunks",
                "tokens": plan.live_chunks.estimated_tokens,
            },
            {
                "name": "ephemeral_recent_comments",
                "tokens": plan.comments_tokens,
            },
            {"name": "ephemeral_recent_chat", "tokens": 0},
            {"name": "current_task", "tokens": plan.task_tokens},
        ],
        "total_estimate": estimated_tokens,
        "safe_total_estimate": safe_total,
        "raw_total_estimate": raw_total,
        "hard_cap": ctx_cfg.emergency_input_cap_tokens,
        "attention_target": ctx_cfg.attention_target_input_tokens,
        "live_chunk_ids": plan.live_chunks.chunk_ids,
        "live_start_paragraph_idx": plan.live_start,
        "frontier_paragraph_idx": plan.frontier,
        "partial_chunk_id": plan.live_chunks.partial_chunk_id,
        "partial_frontier_paragraph_idx": (
            plan.live_chunks.partial_frontier_paragraph_idx
        ),
        "summary_id": plan.summary_id,
        "compaction_epoch": plan.compaction_epoch,
        "context_hash": ctx_hash,
        "preflight_triggered": trigger.preflight_triggered,
        "hard_triggered": trigger.hard_triggered,
        "context_degraded": context_degraded,
        "token_estimator": estimator_info,
    }

    return ContextBuildResult(
        prompt=full_prompt,
        estimated_tokens=estimated_tokens,
        context_hash=ctx_hash,
        prompt_manifest=manifest,
        live_chunk_ids=plan.live_chunks.chunk_ids,
        partial_chunk_id=plan.live_chunks.partial_chunk_id,
        partial_frontier_paragraph_idx=(
            plan.live_chunks.partial_frontier_paragraph_idx
        ),
        summary_id=plan.summary_id,
        compaction_epoch=plan.compaction_epoch,
        preflight_triggered=trigger.preflight_triggered,
        hard_triggered=trigger.hard_triggered,
        context_degraded=context_degraded,
        emergency_overflow_used=emergency_overflow_used,
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
    started = time.monotonic()
    span_attrs = {
        "book.id": book_id,
        "chapter.idx": chapter_idx,
        "paragraph.idx": reading_pidx,
        "task.type": task_type,
    }
    with start_observable_span("service.context.build", span_attrs) as span:
        try:
            ctx_cfg = settings.context
            reader_cfg = settings.reader
            eph_comments_cfg = settings.ephemeral_comments
            est = _get_estimator(settings, token_estimator)

            # --- PLAN ---
            last_pidx = await paragraph_repo.get_last_paragraph_idx(
                db, book_id, chapter_idx
            )
            if last_pidx is None:
                raise ValueError(
                    f"No paragraphs for book={book_id} chapter={chapter_idx}"
                )

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
                live_start = summary_row.covered_end_paragraph_idx + 1

            (
                original_block,
                live_chunk_ids,
                partial_chunk_id,
                partial_frontier_pidx,
                original_tokens,
            ) = await _build_original_block(
                db, book_id, chapter_idx, live_start, frontier
            )

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

            if task_type == "chat":
                task_block, task_tokens = _build_chat_task_block(reading_pidx)
            else:
                task_block, task_tokens = _build_task_block(
                    frontier, focus_start, focus_end, target_paragraphs, density_hint
                )

            system_tokens = 3_000
            metadata_tokens = 800
            reserved_tokens = ctx_cfg.reserved_tokens

            plan = ContextPlan(
                chapter_idx=chapter_idx,
                frontier=frontier,
                live_start=live_start,
                summary_text=summary_text,
                summary_id=summary_id,
                summary_tokens=summary_tokens,
                compaction_epoch=compaction_epoch,
                live_chunks=LiveOriginalChunkSelection(
                    block_text=original_block,
                    chunk_ids=live_chunk_ids,
                    partial_chunk_id=partial_chunk_id,
                    partial_frontier_paragraph_idx=partial_frontier_pidx,
                    estimated_tokens=original_tokens,
                ),
                comments_text=comment_block,
                comments_tokens=ephemeral_comment_tokens,
                task_text=task_block,
                task_tokens=task_tokens,
                book_title=book_title,
                chapter_title=chapter_title,
                system_tokens=system_tokens,
                metadata_tokens=metadata_tokens,
                reserved_tokens=reserved_tokens,
            )

            # --- BUDGET ---
            calibration_agent = "chat" if task_type == "chat" else "comment"
            calibration_model = settings.effective_model_identity(calibration_agent)
            safe_estimated_tokens = (
                est.get_safe_estimate(original_block, calibration_model)
                + system_tokens
                + metadata_tokens
                + reserved_tokens
                + summary_tokens
                + ephemeral_comment_tokens
                + task_tokens
            )

            trigger = await _fetch_trigger_inputs(
                db, book_id, chapter_idx, frontier, safe_estimated_tokens, settings
            )

            plan, estimated_tokens, context_degraded, emergency_now = (
                await _apply_overflow(
                    db,
                    book_id,
                    chapter_idx,
                    plan,
                    safe_estimated_tokens,
                    settings,
                    overflow_already_used,
                    target_paragraphs=target_paragraphs,
                )
            )

            # --- RENDER ---
            result = render_context(
                plan,
                estimated_tokens=estimated_tokens,
                trigger=trigger,
                context_degraded=context_degraded,
                emergency_overflow_used=overflow_already_used or emergency_now,
                estimator=est,
                settings=settings,
                calibration_model=calibration_model,
            )
            duration_ms = (time.monotonic() - started) * 1000
            set_span_attributes(
                span,
                {
                    "ai.context_hash": result.context_hash,
                    "ai.context_tokens": result.estimated_tokens,
                    "context.degraded": result.context_degraded,
                    "context.preflight_triggered": result.preflight_triggered,
                    "context.hard_triggered": result.hard_triggered,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            record_context_build_metric(
                task_type=task_type,
                status="ok",
                duration_ms=duration_ms,
                estimated_tokens=result.estimated_tokens,
                context_degraded=result.context_degraded,
                preflight_triggered=result.preflight_triggered,
                hard_triggered=result.hard_triggered,
            )
            return result
        except Exception as exc:
            duration_ms = (time.monotonic() - started) * 1000
            mark_span_error(span, exc)
            record_context_build_metric(
                task_type=task_type,
                status="error",
                duration_ms=duration_ms,
            )
            raise
