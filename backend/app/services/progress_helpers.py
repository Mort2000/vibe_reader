from __future__ import annotations

from typing import Any

import aiosqlite

from ..repos import paragraphs as paragraph_repo


def compare_reading_positions(
    chapter_a: int,
    paragraph_a: int,
    chapter_b: int,
    paragraph_b: int,
) -> int:
    """Return -1 if A precedes B, 0 if equal, 1 if A follows B."""
    if chapter_a != chapter_b:
        return -1 if chapter_a < chapter_b else 1
    if paragraph_a < paragraph_b:
        return -1
    if paragraph_a > paragraph_b:
        return 1
    return 0


def is_reading_at_least(
    chapter_idx: int,
    paragraph_idx: int,
    *,
    ref_chapter_idx: int,
    ref_paragraph_idx: int,
) -> bool:
    return (
        compare_reading_positions(
            chapter_idx,
            paragraph_idx,
            ref_chapter_idx,
            ref_paragraph_idx,
        )
        >= 0
    )


def detect_jump_type(
    state: dict[str, Any],
    chapter_idx: int,
    paragraph_idx: int,
) -> str:
    current_ch = state.get("active_chapter_idx", 0)
    current_p = state.get("reading_paragraph_idx", 0)
    if chapter_idx < current_ch:
        return "backward"
    if chapter_idx == current_ch and paragraph_idx < current_p:
        return "backward"
    ctx_ch = state.get("context_frontier_chapter_idx", 0)
    ctx_p = state.get("context_frontier_paragraph_idx", 0)
    if chapter_idx > ctx_ch:
        return "forward"
    if chapter_idx == ctx_ch and paragraph_idx > ctx_p:
        return "forward"
    return "normal"


async def check_forward_jump_chars(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    state: dict[str, Any],
    new_frontier: int,
) -> int:
    """Count chars in (context_frontier, new_assistant_frontier] per design §6.

    ``count_chars_in_range`` uses an exclusive start (paragraph_idx > start),
    so ``context_frontier_paragraph_idx`` itself is not double-counted.
    """
    ctx_ch = state.get("context_frontier_chapter_idx", 0)
    ctx_p = state.get("context_frontier_paragraph_idx", 0)

    if chapter_idx > ctx_ch:
        old_last = await paragraph_repo.get_last_paragraph_idx(db, book_id, ctx_ch)
        old_remaining = await paragraph_repo.count_chars_in_range(
            db,
            book_id,
            ctx_ch,
            ctx_p,
            old_last or ctx_p,
        )
        new_prefix = await paragraph_repo.count_chars_in_range(
            db,
            book_id,
            chapter_idx,
            -1,
            new_frontier,
        )
        return old_remaining + new_prefix

    return await paragraph_repo.count_chars_in_range(
        db,
        book_id,
        chapter_idx,
        ctx_p,
        new_frontier,
    )


async def compute_assistant_frontier(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    lookahead_paragraphs: int,
) -> int:
    last_p = await paragraph_repo.get_last_paragraph_idx(db, book_id, chapter_idx)
    return min(
        paragraph_idx + lookahead_paragraphs,
        last_p if last_p is not None else paragraph_idx,
    )


async def validate_pending_progress(
    db: aiosqlite.Connection,
    book_id: int,
    state: dict[str, Any],
    *,
    chapter_idx: int,
    paragraph_idx: int,
    assistant_frontier_chapter_idx: int | None,
    assistant_frontier_paragraph_idx: int | None,
    max_context_jump_chars: int,
    lookahead_paragraphs: int,
) -> tuple[str, int]:
    """Validate pending progress before replay. Returns (jump_type, jump_chars)."""
    jump_type = detect_jump_type(state, chapter_idx, paragraph_idx)
    if jump_type == "backward":
        return jump_type, 0

    if assistant_frontier_paragraph_idx is not None:
        new_frontier = assistant_frontier_paragraph_idx
        af_ch = (
            assistant_frontier_chapter_idx
            if assistant_frontier_chapter_idx is not None
            else chapter_idx
        )
        if af_ch != chapter_idx:
            new_frontier = await compute_assistant_frontier(
                db,
                book_id,
                chapter_idx,
                paragraph_idx,
                lookahead_paragraphs,
            )
    else:
        new_frontier = await compute_assistant_frontier(
            db,
            book_id,
            chapter_idx,
            paragraph_idx,
            lookahead_paragraphs,
        )

    jump_chars = 0
    if jump_type == "forward":
        jump_chars = await check_forward_jump_chars(
            db,
            book_id,
            chapter_idx,
            state,
            new_frontier,
        )
        if jump_chars > max_context_jump_chars:
            return "forward_rejected", jump_chars

    return jump_type, jump_chars
