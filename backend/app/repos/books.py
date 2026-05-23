from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def create_book(
    db: aiosqlite.Connection,
    *,
    title: str,
    author: str | None,
    file_hash: str,
    file_path: str,
    cover_path: str | None,
    total_chapters: int,
) -> dict[str, Any]:
    now = _now()
    cur = await db.execute(
        """INSERT INTO books (title, author, file_hash, file_path, cover_path, total_chapters, imported_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, author, file_hash, file_path, cover_path, total_chapters, now, now),
    )
    await db.commit()
    book_id = cur.lastrowid
    return {
        "id": book_id,
        "title": title,
        "author": author,
        "file_hash": file_hash,
        "file_path": file_path,
        "cover_path": cover_path,
        "total_chapters": total_chapters,
        "imported_at": now,
        "updated_at": now,
    }


async def get_book_by_hash(
    db: aiosqlite.Connection, file_hash: str
) -> dict[str, Any] | None:
    cur = await db.execute("SELECT * FROM books WHERE file_hash = ?", (file_hash,))
    row = await cur.fetchone()
    if row is None:
        return None
    return dict(row)


async def get_book(db: aiosqlite.Connection, book_id: int) -> dict[str, Any] | None:
    cur = await db.execute("SELECT * FROM books WHERE id = ?", (book_id,))
    row = await cur.fetchone()
    if row is None:
        return None
    return dict(row)


async def list_books(
    db: aiosqlite.Connection,
    *,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where = ""
    params: list[Any] = []
    if q:
        where = "WHERE title LIKE ? OR author LIKE ?"
        params.extend([f"%{q}%", f"%{q}%"])

    count_cur = await db.execute(f"SELECT COUNT(*) FROM books {where}", params)
    total = (await count_cur.fetchone())[0]

    cur = await db.execute(
        f"SELECT * FROM books {where} ORDER BY imported_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows], total


async def delete_book(db: aiosqlite.Connection, book_id: int) -> bool:
    cur = await db.execute("DELETE FROM books WHERE id = ?", (book_id,))
    await db.commit()
    return cur.rowcount > 0


async def update_book_chapters(
    db: aiosqlite.Connection, book_id: int, total_chapters: int
) -> None:
    await db.execute(
        "UPDATE books SET total_chapters = ?, updated_at = ? WHERE id = ?",
        (total_chapters, _now(), book_id),
    )
    await db.commit()
