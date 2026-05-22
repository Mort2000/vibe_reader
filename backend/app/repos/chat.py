from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def get_or_create_session(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    chapter_idx: int,
) -> dict[str, Any]:
    cur = await db.execute(
        "SELECT * FROM chat_sessions WHERE book_id = ? AND chapter_idx = ? ORDER BY updated_at DESC LIMIT 1",
        (book_id, chapter_idx),
    )
    row = await cur.fetchone()
    if row:
        return dict(row)

    now = _now()
    cur = await db.execute(
        """INSERT INTO chat_sessions (book_id, chapter_idx, title, last_paragraph_idx, created_at, updated_at)
           VALUES (?, ?, NULL, 0, ?, ?)""",
        (book_id, chapter_idx, now, now),
    )
    await db.commit()
    return {
        "id": cur.lastrowid,
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "title": None,
        "last_paragraph_idx": 0,
        "created_at": now,
        "updated_at": now,
    }


async def create_turn(
    db: aiosqlite.Connection,
    *,
    session_id: int,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    user_msg: str,
    status: str = "streaming",
) -> dict[str, Any]:
    now = _now()
    cur = await db.execute(
        """INSERT INTO chat_turns (session_id, book_id, chapter_idx, paragraph_idx, user_msg, ai_msg, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
        (session_id, book_id, chapter_idx, paragraph_idx, user_msg, status, now, now),
    )
    await db.commit()
    return {
        "id": cur.lastrowid,
        "session_id": session_id,
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "paragraph_idx": paragraph_idx,
        "user_msg": user_msg,
        "ai_msg": None,
        "status": status,
        "tokens_in": None,
        "tokens_out": None,
        "trace_id": None,
        "created_at": now,
        "updated_at": now,
    }


async def update_turn(
    db: aiosqlite.Connection,
    turn_id: int,
    *,
    ai_msg: str | None = None,
    status: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    trace_id: str | None = None,
) -> None:
    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [_now()]
    if ai_msg is not None:
        sets.append("ai_msg = ?")
        params.append(ai_msg)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if tokens_in is not None:
        sets.append("tokens_in = ?")
        params.append(tokens_in)
    if tokens_out is not None:
        sets.append("tokens_out = ?")
        params.append(tokens_out)
    if trace_id is not None:
        sets.append("trace_id = ?")
        params.append(trace_id)

    params.append(turn_id)
    await db.execute(f"UPDATE chat_turns SET {', '.join(sets)} WHERE id = ?", params)
    await db.commit()


async def list_turns(
    db: aiosqlite.Connection,
    session_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    count_cur = await db.execute(
        "SELECT COUNT(*) FROM chat_turns WHERE session_id = ?", (session_id,)
    )
    total = (await count_cur.fetchone())[0]

    cur = await db.execute(
        "SELECT * FROM chat_turns WHERE session_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (session_id, limit, offset),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows], total
