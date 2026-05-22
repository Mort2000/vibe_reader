from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def create_chapter(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    idx: int,
    title: str,
    raw_text: str = "",
    paragraph_count: int = 0,
    token_estimate: int = 0,
    analysis_status: str = "pending",
) -> dict[str, Any]:
    now = _now()
    cur = await db.execute(
        """INSERT INTO chapters (book_id, idx, title, raw_text, paragraph_count, token_estimate, analysis_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(book_id, idx) DO UPDATE SET
               title=excluded.title, raw_text=excluded.raw_text,
               paragraph_count=excluded.paragraph_count,
               token_estimate=excluded.token_estimate, updated_at=excluded.updated_at""",
        (book_id, idx, title, raw_text, paragraph_count, token_estimate, analysis_status, now, now),
    )
    await db.commit()
    return {
        "book_id": book_id,
        "idx": idx,
        "title": title,
        "raw_text": raw_text,
        "paragraph_count": paragraph_count,
        "token_estimate": token_estimate,
        "analysis_status": analysis_status,
    }


async def get_chapter(db: aiosqlite.Connection, book_id: int, idx: int) -> dict[str, Any] | None:
    cur = await db.execute(
        "SELECT * FROM chapters WHERE book_id = ? AND idx = ?",
        (book_id, idx),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return dict(row)


async def list_chapters(db: aiosqlite.Connection, book_id: int) -> list[dict[str, Any]]:
    cur = await db.execute(
        "SELECT * FROM chapters WHERE book_id = ? ORDER BY idx",
        (book_id,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def update_chapter_stats(
    db: aiosqlite.Connection,
    book_id: int,
    idx: int,
    *,
    paragraph_count: int,
    token_estimate: int,
) -> None:
    await db.execute(
        "UPDATE chapters SET paragraph_count = ?, token_estimate = ?, updated_at = ? WHERE book_id = ? AND idx = ?",
        (paragraph_count, token_estimate, _now(), book_id, idx),
    )
    await db.commit()
