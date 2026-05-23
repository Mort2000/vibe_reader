from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiosqlite

from ..config import Settings
from ..repos import paragraphs as paragraph_repo
from ..repos import windows as window_repo

logger = logging.getLogger(__name__)


def compute_assistant_frontier(
    reading_pidx: int, lookahead: int, last_pidx: int
) -> int:
    return min(reading_pidx + lookahead, last_pidx)


def compute_text_hash(paragraphs: list[dict[str, Any]]) -> str:
    combined = "".join(p.get("text", "") for p in paragraphs)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def _should_advance(
    window: dict[str, Any],
    reading_pidx: int,
    trigger_ratio: float,
) -> bool:
    start = window["start_paragraph_idx"]
    end = window["end_paragraph_idx"]
    span = max(1, end - start)
    progress = (reading_pidx - start) / span
    return progress >= trigger_ratio


async def _compute_window_bounds(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    focus_start: int,
    focus_end: int,
    wc: Any,
) -> tuple[list[dict[str, Any]], int, int]:
    start_pidx = max(0, focus_start - wc.overlap_paragraphs)
    end_pidx = focus_end

    window_paragraphs = await paragraph_repo.get_paragraphs_range(
        db, book_id, chapter_idx, start_pidx, end_pidx
    )

    token_sum = sum(p.get("token_estimate", 0) for p in window_paragraphs)

    while (
        start_pidx > 0
        and len(window_paragraphs) < wc.min_focus_paragraphs
    ):
        start_pidx -= 1
        p = await paragraph_repo.get_paragraph(db, book_id, chapter_idx, start_pidx)
        if p:
            window_paragraphs.insert(0, p)
            token_sum += p.get("token_estimate", 0)
        if token_sum > wc.focus_max_tokens:
            window_paragraphs.pop(0)
            start_pidx += 1
            break

    if token_sum > wc.focus_max_tokens:
        while token_sum > wc.focus_max_tokens and end_pidx > focus_start:
            removed = window_paragraphs.pop()
            token_sum -= removed.get("token_estimate", 0)
            end_pidx = removed["paragraph_idx"] - 1
        token_sum = sum(p.get("token_estimate", 0) for p in window_paragraphs)

    if len(window_paragraphs) > wc.max_focus_paragraphs:
        window_paragraphs = window_paragraphs[: wc.max_focus_paragraphs]
        end_pidx = window_paragraphs[-1]["paragraph_idx"]

    return window_paragraphs, start_pidx, end_pidx


async def get_or_create_window(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    reading_pidx: int,
    settings: Settings,
) -> tuple[dict[str, Any], bool]:
    last_pidx = await paragraph_repo.get_last_paragraph_idx(db, book_id, chapter_idx)
    if last_pidx is None:
        raise ValueError(f"No paragraphs for book={book_id} chapter={chapter_idx}")

    latest_window = await window_repo.find_latest_window(db, book_id, chapter_idx)

    wc = settings.window_l1

    if latest_window is not None:
        start = latest_window["start_paragraph_idx"]
        end = latest_window["end_paragraph_idx"]
        in_range = start <= reading_pidx <= end
        status_ok = latest_window["status"] in ("pending", "running", "done")
        if in_range and status_ok and not _should_advance(
            latest_window, reading_pidx, wc.trigger_advance_ratio
        ):
            return latest_window, False

    frontier = compute_assistant_frontier(
        reading_pidx, settings.reader.lookahead_paragraphs, last_pidx
    )

    focus_end = frontier
    prev_done = (
        latest_window is not None
        and latest_window["status"] in ("pending", "running", "done")
    )
    focus_start = (latest_window["focus_end_paragraph_idx"] + 1) if prev_done else reading_pidx
    if focus_start > focus_end:
        focus_start = focus_end

    window_paragraphs, start_pidx, end_pidx = await _compute_window_bounds(
        db, book_id, chapter_idx, focus_start, focus_end, wc
    )

    text_hash = compute_text_hash(window_paragraphs)

    if latest_window is not None and latest_window.get("text_hash") == text_hash:
        return latest_window, False

    window_seq = (
        latest_window["window_seq"] + 1 if latest_window else 0
    )

    window = await window_repo.create_window(
        db,
        book_id=book_id,
        chapter_idx=chapter_idx,
        window_seq=window_seq,
        start_paragraph_idx=start_pidx,
        end_paragraph_idx=end_pidx,
        focus_start_paragraph_idx=focus_start,
        focus_end_paragraph_idx=focus_end,
        assistant_frontier_paragraph_idx=frontier,
        text_hash=text_hash,
    )

    logger.info(
        "window.created",
        extra={
            "event": "window.created",
            "fields": {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "window_id": window["id"],
                "window_seq": window_seq,
                "start": start_pidx,
                "end": end_pidx,
                "focus_start": focus_start,
                "focus_end": focus_end,
                "frontier": frontier,
                "paragraph_count": len(window_paragraphs),
            },
        },
    )

    return window, True
