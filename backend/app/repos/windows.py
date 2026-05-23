from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def create_window(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    chapter_idx: int,
    window_seq: int,
    start_paragraph_idx: int,
    end_paragraph_idx: int,
    focus_start_paragraph_idx: int,
    focus_end_paragraph_idx: int,
    assistant_frontier_paragraph_idx: int,
    text_hash: str = "",
    context_hash: str = "",
) -> dict[str, Any]:
    now = _now()
    cur = await db.execute(
        """INSERT INTO reading_windows
           (book_id, chapter_idx, window_seq, start_paragraph_idx, end_paragraph_idx,
            focus_start_paragraph_idx, focus_end_paragraph_idx, assistant_frontier_paragraph_idx,
            text_hash, context_hash, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            book_id,
            chapter_idx,
            window_seq,
            start_paragraph_idx,
            end_paragraph_idx,
            focus_start_paragraph_idx,
            focus_end_paragraph_idx,
            assistant_frontier_paragraph_idx,
            text_hash,
            context_hash,
            now,
            now,
        ),
    )
    await db.commit()
    return {
        "id": cur.lastrowid,
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "window_seq": window_seq,
        "start_paragraph_idx": start_paragraph_idx,
        "end_paragraph_idx": end_paragraph_idx,
        "focus_start_paragraph_idx": focus_start_paragraph_idx,
        "focus_end_paragraph_idx": focus_end_paragraph_idx,
        "assistant_frontier_paragraph_idx": assistant_frontier_paragraph_idx,
        "status": "pending",
        "error": None,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }


async def get_window(db: aiosqlite.Connection, window_id: int) -> dict[str, Any] | None:
    cur = await db.execute("SELECT * FROM reading_windows WHERE id = ?", (window_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def find_latest_window(
    db: aiosqlite.Connection, book_id: int, chapter_idx: int
) -> dict[str, Any] | None:
    cur = await db.execute(
        "SELECT * FROM reading_windows WHERE book_id = ? AND chapter_idx = ? ORDER BY window_seq DESC LIMIT 1",
        (book_id, chapter_idx),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def update_window_status(
    db: aiosqlite.Connection,
    window_id: int,
    status: str,
    *,
    error: str | None = None,
) -> None:
    now = _now()
    completed_at = now if status in ("done", "failed") else None
    await db.execute(
        "UPDATE reading_windows SET status = ?, error = ?, updated_at = ?, completed_at = ? WHERE id = ?",
        (status, error, now, completed_at, window_id),
    )
    await db.commit()
