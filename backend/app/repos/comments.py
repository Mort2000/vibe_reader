from __future__ import annotations

from typing import Any

import aiosqlite


async def create_comment(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    window_id: int,
    comment: str,
    comment_type: str = "observation",
    trace_id: str | None = None,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = await db.execute(
        """INSERT INTO paragraph_comments
           (book_id, chapter_idx, paragraph_idx, window_id, comment, comment_type, status, trace_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
        (
            book_id,
            chapter_idx,
            paragraph_idx,
            window_id,
            comment,
            comment_type,
            trace_id,
            now,
            now,
        ),
    )
    await db.commit()
    return {
        "id": cur.lastrowid,
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "paragraph_idx": paragraph_idx,
        "window_id": window_id,
        "comment": comment,
        "comment_type": comment_type,
        "status": "active",
        "trace_id": trace_id,
        "created_at": now,
        "updated_at": now,
    }


async def list_comments(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    *,
    start: int | None = None,
    end: int | None = None,
    status: str = "active",
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    conditions = ["book_id = ?", "chapter_idx = ?"]
    params: list[Any] = [book_id, chapter_idx]

    if status != "all":
        conditions.append("status = ?")
        params.append(status)

    if start is not None:
        conditions.append("paragraph_idx >= ?")
        params.append(start)
    if end is not None:
        conditions.append("paragraph_idx <= ?")
        params.append(end)

    where = " AND ".join(conditions)

    count_cur = await db.execute(
        f"SELECT COUNT(*) FROM paragraph_comments WHERE {where}", params
    )
    total = (await count_cur.fetchone())[0]

    sql = f"SELECT * FROM paragraph_comments WHERE {where} ORDER BY paragraph_idx, created_at"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    cur = await db.execute(sql, params)
    rows = await cur.fetchall()
    return [dict(r) for r in rows], total


async def delete_comments_by_window(
    db: aiosqlite.Connection,
    window_id: int,
) -> int:
    cur = await db.execute(
        "DELETE FROM paragraph_comments WHERE window_id = ?",
        (window_id,),
    )
    await db.commit()
    return cur.rowcount


async def get_comments_by_paragraph(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
) -> list[dict[str, Any]]:
    cur = await db.execute(
        "SELECT * FROM paragraph_comments WHERE book_id = ? AND chapter_idx = ? AND paragraph_idx = ? AND status = 'active' ORDER BY created_at DESC",
        (book_id, chapter_idx, paragraph_idx),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]
