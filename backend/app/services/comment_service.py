from __future__ import annotations

import logging
import time
from typing import Any

import aiosqlite

from ..config import Settings
from ..observability import get_trace_id
from ..repos import books as book_repo
from ..repos import chapters as chapter_repo
from ..repos import comments as comment_repo
from ..repos import paragraphs as paragraph_repo
from .agent_base import ParagraphCommentBatch, get_comment_agent
from .job_runner import JobRunner

logger = logging.getLogger(__name__)


def build_comment_prompt(
    book_meta: dict[str, Any],
    chapter_meta: dict[str, Any],
    window_paragraphs: list[dict[str, Any]],
    target_paragraphs: list[int],
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

    return "\n".join(lines)


def _validate_and_dedupe(
    batch: ParagraphCommentBatch,
    target_set: set[int],
) -> list[dict[str, Any]]:
    seen: set[int] = set()
    valid: list[dict[str, Any]] = []

    for draft in batch.comments:
        if draft.paragraph_idx not in target_set:
            continue
        if not draft.comment.strip():
            continue
        if draft.paragraph_idx in seen:
            continue
        seen.add(draft.paragraph_idx)
        valid.append(
            {
                "paragraph_idx": draft.paragraph_idx,
                "comment": draft.comment.strip(),
                "comment_type": draft.comment_type,
            }
        )

    return valid


async def run_comment_task(
    db: aiosqlite.Connection,
    job_id: int,
    window: dict[str, Any] | None,
    settings: Settings,
) -> None:
    if window is None:
        raise ValueError(f"Window not found for job {job_id}")

    window_id = window["id"]
    book_id = window["book_id"]
    chapter_idx = window["chapter_idx"]

    focus_start = window["focus_start_paragraph_idx"]
    focus_end = window["focus_end_paragraph_idx"]
    start_pidx = window["start_paragraph_idx"]
    end_pidx = window["end_paragraph_idx"]

    window_paragraphs = await paragraph_repo.get_paragraphs_range(
        db, book_id, chapter_idx, start_pidx, end_pidx
    )

    target_paragraphs = list(range(focus_start, focus_end + 1))
    target_set = set(target_paragraphs)

    book = await book_repo.get_book(db, book_id)
    chapter = await chapter_repo.get_chapter(db, book_id, chapter_idx)
    if not book or not chapter:
        raise ValueError(f"Book/chapter not found: {book_id}/{chapter_idx}")

    prompt = build_comment_prompt(
        book_meta=book,
        chapter_meta=chapter,
        window_paragraphs=window_paragraphs,
        target_paragraphs=target_paragraphs,
    )

    await comment_repo.delete_comments_by_window(db, window_id)

    agent = get_comment_agent(settings)
    trace_id = get_trace_id()

    t0 = time.monotonic()
    result = await agent.run(
        prompt,
        metadata={
            "book_id": book_id,
            "chapter_idx": chapter_idx,
            "window_id": window_id,
            "trace_id": trace_id,
        },
    )
    latency_ms = (time.monotonic() - t0) * 1000

    usage = result.usage()
    batch: ParagraphCommentBatch = result.output
    valid_comments = _validate_and_dedupe(batch, target_set)

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

    logger.info(
        "comment_task.completed",
        extra={
            "event": "comment_task.completed",
            "fields": {
                "job_id": job_id,
                "window_id": window_id,
                "target_count": len(target_set),
                "generated_count": len(batch.comments),
                "saved_count": len(valid_comments),
                "latency_ms": round(latency_ms, 1),
                "request_tokens": usage.request_tokens,
                "response_tokens": usage.response_tokens,
                "total_tokens": (usage.request_tokens or 0) + (usage.response_tokens or 0),
            },
        },
    )


def register_with_runner(runner: JobRunner) -> None:
    runner.register_handler("comment_window", run_comment_task)
