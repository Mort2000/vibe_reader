from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def get_progress(db: aiosqlite.Connection, book_id: int) -> dict[str, Any]:
    cur = await db.execute(
        "SELECT * FROM reading_progress WHERE book_id = ?", (book_id,)
    )
    row = await cur.fetchone()
    if row is None:
        return {
            "book_id": book_id,
            "chapter_idx": 0,
            "paragraph_idx": 0,
            "scroll_pct": 0.0,
            "updated_at": None,
        }
    return dict(row)


async def upsert_progress(
    db: aiosqlite.Connection,
    book_id: int,
    *,
    chapter_idx: int,
    paragraph_idx: int,
    scroll_pct: float,
) -> dict[str, Any]:
    now = _now()
    await db.execute(
        """INSERT INTO reading_progress (book_id, chapter_idx, paragraph_idx, scroll_pct, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(book_id) DO UPDATE SET
               chapter_idx=excluded.chapter_idx,
               paragraph_idx=excluded.paragraph_idx,
               scroll_pct=excluded.scroll_pct,
               updated_at=excluded.updated_at""",
        (book_id, chapter_idx, paragraph_idx, scroll_pct, now),
    )
    await db.commit()
    return {
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "paragraph_idx": paragraph_idx,
        "scroll_pct": scroll_pct,
        "updated_at": now,
    }
