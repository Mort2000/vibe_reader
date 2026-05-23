from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def bulk_insert_paragraphs(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    paragraphs: list[dict[str, Any]],
) -> int:
    """Insert paragraphs. Returns count inserted."""
    if not paragraphs:
        return 0
    rows = [
        (
            book_id,
            chapter_idx,
            p["paragraph_idx"],
            p["text"],
            p["text_hash"],
            p["char_count"],
            p["token_estimate"],
            _now(),
        )
        for p in paragraphs
    ]
    await db.executemany(
        """INSERT INTO paragraphs (book_id, chapter_idx, paragraph_idx, text, text_hash, char_count, token_estimate, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(book_id, chapter_idx, paragraph_idx) DO UPDATE SET
               text=excluded.text, text_hash=excluded.text_hash,
               char_count=excluded.char_count, token_estimate=excluded.token_estimate""",
        rows,
    )
    await db.commit()
    return len(rows)


async def list_paragraphs(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    *,
    start: int = 0,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    count_cur = await db.execute(
        "SELECT COUNT(*) FROM paragraphs WHERE book_id = ? AND chapter_idx = ?",
        (book_id, chapter_idx),
    )
    total = (await count_cur.fetchone())[0]

    sql = "SELECT * FROM paragraphs WHERE book_id = ? AND chapter_idx = ? ORDER BY paragraph_idx"
    params: list[Any] = [book_id, chapter_idx]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, start])

    cur = await db.execute(sql, params)
    rows = await cur.fetchall()
    return [dict(r) for r in rows], total


async def get_paragraph(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
) -> dict[str, Any] | None:
    cur = await db.execute(
        "SELECT * FROM paragraphs WHERE book_id = ? AND chapter_idx = ? AND paragraph_idx = ?",
        (book_id, chapter_idx, paragraph_idx),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return dict(row)


async def get_last_paragraph_idx(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
) -> int | None:
    cur = await db.execute(
        "SELECT MAX(paragraph_idx) as mx FROM paragraphs WHERE book_id = ? AND chapter_idx = ?",
        (book_id, chapter_idx),
    )
    row = await cur.fetchone()
    return row["mx"] if row and row["mx"] is not None else None


async def get_paragraphs_range(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    start_idx: int,
    end_idx: int,
) -> list[dict[str, Any]]:
    cur = await db.execute(
        "SELECT * FROM paragraphs WHERE book_id = ? AND chapter_idx = ? AND paragraph_idx >= ? AND paragraph_idx <= ? ORDER BY paragraph_idx",
        (book_id, chapter_idx, start_idx, end_idx),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_chars_in_range(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    start_idx: int,
    end_idx: int,
) -> int:
    cur = await db.execute(
        "SELECT COALESCE(SUM(char_count), 0) FROM paragraphs "
        "WHERE book_id = ? AND chapter_idx = ? "
        "AND paragraph_idx > ? AND paragraph_idx <= ?",
        (book_id, chapter_idx, start_idx, end_idx),
    )
    row = await cur.fetchone()
    return row[0] if row else 0
