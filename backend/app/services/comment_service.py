from __future__ import annotations

import logging
import time
from typing import Any

import aiosqlite

from ..config import Settings
from ..observability import ensure_trace_id
from ..repos import books as book_repo
from ..repos import chapters as chapter_repo
from ..repos import comments as comment_repo
from ..repos import paragraphs as paragraph_repo
from .agent_base import CommentDensityHint, CommentDeps, EmitCommentDraft
from .agent_base import get_comment_agent
from .job_runner import JobRunner

logger = logging.getLogger(__name__)


def _build_density_hint(
    active_count: int,
    stat_start: int,
    stat_end: int,
    total_target_paragraphs: int,
    soft_min: float,
) -> CommentDensityHint:
    current_density = active_count / max(1, total_target_paragraphs)
    estimated_missing = max(
        0, int(total_target_paragraphs * soft_min) - active_count
    )
    return CommentDensityHint(
        stat_start_paragraph_idx=stat_start,
        stat_end_paragraph_idx=stat_end,
        stat_target_paragraph_count=total_target_paragraphs,
        active_comment_count=active_count,
        soft_min_density=soft_min,
        current_density=round(current_density, 4),
        estimated_missing_comments=estimated_missing,
    )


def build_comment_prompt(
    book_meta: dict[str, Any],
    chapter_meta: dict[str, Any],
    window_paragraphs: list[dict[str, Any]],
    target_paragraphs: list[int],
    density_hint: CommentDensityHint | None = None,
) -> str:
    lines: list[str] = []

    lines.append(f"书籍：{book_meta.get('title', '未知')}")
    chapter_title = chapter_meta.get("title", f"第{chapter_meta.get('idx', 0)}章")
    lines.append(f"章节：{chapter_title}")
    lines.append("")

    lines.append("<CURRENT_WINDOW>")
    for p in window_paragraphs:
        idx = p["paragraph_idx"]
        marker = " ★" if idx in target_paragraphs else ""
        lines.append(f"[P{idx}]{marker} {p['text']}")
    lines.append("</CURRENT_WINDOW>")
    lines.append("")

    target_str = ", ".join(str(i) for i in sorted(target_paragraphs))
    lines.append(f"comment_target_paragraphs = [{target_str}]")
    lines.append("请仅为上述标有 ★ 且列在 comment_target_paragraphs 中的段落生成评论。")
    lines.append("每个目标段落生成一条评论。如果某个段落信息不足以评论，可以跳过。")

    if density_hint is not None:
        lines.append("")
        lines.append("comment_density_hint:")
        lines.append(f"  stat_start_paragraph_idx = {density_hint.stat_start_paragraph_idx}")
        lines.append(f"  stat_end_paragraph_idx = {density_hint.stat_end_paragraph_idx}")
        lines.append(
            f"  stat_target_paragraph_count = {density_hint.stat_target_paragraph_count}"
        )
        lines.append(f"  active_comment_count = {density_hint.active_comment_count}")
        lines.append(f"  soft_min_density = {density_hint.soft_min_density}")
        lines.append(f"  current_density = {density_hint.current_density}")
        lines.append(
            f"  estimated_missing_comments = {density_hint.estimated_missing_comments}"
        )

    return "\n".join(lines)


def _validate_and_dedupe(
    raw_payloads: list[dict[str, Any]],
    target_set: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], int]:
    seen: set[int] = set()
    valid: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    discarded_by_reason: dict[str, int] = {}
    validation_failed_count = 0

    def discard(payload: dict[str, Any], reason: str) -> None:
        discarded.append({"payload": payload, "reason": reason})
        discarded_by_reason[reason] = discarded_by_reason.get(reason, 0) + 1

    for payload in raw_payloads:
        try:
            draft = EmitCommentDraft.model_validate(payload)
        except Exception:
            validation_failed_count += 1
            discard(payload, "validation_failed")
            continue

        if draft.paragraph_idx not in target_set:
            discard(payload, "out_of_target")
            continue
        if not draft.comment.strip():
            discard(payload, "empty_comment")
            continue
        if draft.paragraph_idx in seen:
            discard(payload, "duplicate_paragraph")
            continue
        seen.add(draft.paragraph_idx)
        valid.append(
            {
                "paragraph_idx": draft.paragraph_idx,
                "comment": draft.comment.strip(),
                "comment_type": draft.comment_type,
            }
        )

    return valid, discarded, discarded_by_reason, validation_failed_count


async def run_comment_task(
    db: aiosqlite.Connection,
    job_id: int,
    window: dict[str, Any] | None,
    settings: Settings,
) -> dict[str, Any] | None:
    if window is None:
        raise ValueError(f"Window not found for job {job_id}")

    window_id = window["id"]
    book_id = window["book_id"]
    chapter_idx = window["chapter_idx"]

    focus_start = window["focus_start_paragraph_idx"]
    focus_end = window["focus_end_paragraph_idx"]
    start_pidx = window["start_paragraph_idx"]
    end_pidx = window["end_paragraph_idx"]
    frontier = window["assistant_frontier_paragraph_idx"]

    window_paragraphs = await paragraph_repo.get_paragraphs_range(
        db, book_id, chapter_idx, start_pidx, end_pidx
    )

    target_paragraphs = list(range(focus_start, focus_end + 1))
    target_set = set(target_paragraphs)

    book = await book_repo.get_book(db, book_id)
    chapter = await chapter_repo.get_chapter(db, book_id, chapter_idx)
    if not book or not chapter:
        raise ValueError(f"Book/chapter not found: {book_id}/{chapter_idx}")

    wc = settings.window
    stat_start = max(0, frontier - wc.comment_density_stat_window_paragraphs)
    stat_end = frontier

    active_count = await comment_repo.count_active_comments_in_range(
        db, book_id, chapter_idx, stat_start, stat_end
    )

    total_in_range = await paragraph_repo.get_paragraphs_range(
        db, book_id, chapter_idx, stat_start, stat_end
    )
    stat_target_count = len(total_in_range)

    density_hint = _build_density_hint(
        active_count=active_count,
        stat_start=stat_start,
        stat_end=stat_end,
        total_target_paragraphs=stat_target_count,
        soft_min=wc.comment_density_soft_min,
    )

    prompt = build_comment_prompt(
        book_meta=book,
        chapter_meta=chapter,
        window_paragraphs=window_paragraphs,
        target_paragraphs=target_paragraphs,
        density_hint=density_hint,
    )

    await comment_repo.delete_comments_by_window(db, window_id)

    deps = CommentDeps(
        target_paragraph_ids=target_set,
        density_hint=density_hint,
    )

    agent = get_comment_agent(settings)
    trace_id = ensure_trace_id()

    t0 = time.monotonic()
    result = await agent.run(
        prompt,
        deps=deps,
        metadata={
            "book_id": book_id,
            "chapter_idx": chapter_idx,
            "window_id": window_id,
            "trace_id": trace_id,
        },
    )
    latency_ms = (time.monotonic() - t0) * 1000

    usage = result.usage()

    raw_payloads = deps.raw_tool_payloads
    valid_comments, discarded, discarded_by_reason, validation_failed_count = (
        _validate_and_dedupe(raw_payloads, target_set)
    )

    no_call = len(raw_payloads) == 0

    for c in valid_comments:
        await comment_repo.create_comment(
            db,
            book_id=book_id,
            chapter_idx=chapter_idx,
            paragraph_idx=c["paragraph_idx"],
            window_id=window_id,
            comment=c["comment"],
            comment_type=c["comment_type"],
            trace_id=trace_id,
        )

    log_fields: dict[str, Any] = {
        "job_id": job_id,
        "window_id": window_id,
        "target_count": len(target_set),
        "tool_call_count": len(raw_payloads),
        "valid_count": len(valid_comments),
        "validation_failed_count": validation_failed_count,
        "discarded_count": len(discarded),
        "discarded_by_reason": discarded_by_reason,
        "no_call": no_call,
        "latency_ms": round(latency_ms, 1),
        "request_tokens": usage.request_tokens,
        "response_tokens": usage.response_tokens,
        "total_tokens": (usage.request_tokens or 0) + (usage.response_tokens or 0),
        "comment_density_actual": density_hint.current_density,
        "comment_density_soft_min": density_hint.soft_min_density,
        "comment_density_stat_start": density_hint.stat_start_paragraph_idx,
        "comment_density_stat_end": density_hint.stat_end_paragraph_idx,
    }

    logger.info(
        "comment_task.completed",
        extra={
            "event": "comment_task.completed",
            "fields": log_fields,
        },
    )

    if discarded:
        logger.warning(
            "comment_task.discarded_comments",
            extra={
                "event": "comment_task.discarded_comments",
                "fields": {
                    "window_id": window_id,
                    "trace_id": trace_id,
                    "discarded_count": len(discarded),
                    "discarded_by_reason": discarded_by_reason,
                },
            },
        )

    # PydanticAI RunUsage exposes both legacy (request/response_tokens) and
    # current (input/output_tokens) names; prefer legacy when set.
    usage_input = usage.request_tokens if usage.request_tokens is not None else usage.input_tokens
    usage_output = (
        usage.response_tokens if usage.response_tokens is not None else usage.output_tokens
    )

    return {
        "agent_name": "ParagraphCommentAgent",
        "duration_ms": round(latency_ms, 1),
        "input_tokens": usage_input,
        "output_tokens": usage_output,
        "cached_input_tokens": usage.cache_read_tokens or None,
        "no_call": no_call,
        "tool_call_count": len(raw_payloads),
        "valid_count": len(valid_comments),
        "validation_failed_count": validation_failed_count,
        "discarded_count": len(discarded),
        "discarded_by_reason": discarded_by_reason,
        "candidate_lookup_count": len(target_set),
        "context_hash": window.get("context_hash") or "",
        "comment_density_actual": density_hint.current_density,
        "comment_density_soft_min": density_hint.soft_min_density,
        "density_stat_start": density_hint.stat_start_paragraph_idx,
        "density_stat_end": density_hint.stat_end_paragraph_idx,
    }


def register_with_runner(runner: JobRunner) -> None:
    runner.register_handler("comment_window", run_comment_task)
