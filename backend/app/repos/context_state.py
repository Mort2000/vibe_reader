from __future__ import annotations

import json
from typing import Any

import aiosqlite

_UNSET = object()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def get_or_create(
    db: aiosqlite.Connection, book_id: int
) -> dict[str, Any]:
    cur = await db.execute(
        "SELECT * FROM book_context_states WHERE book_id = ?",
        (book_id,),
    )
    row = await cur.fetchone()
    if row:
        return dict(row)

    now = _now()
    cur = await db.execute(
        """INSERT INTO book_context_states
           (book_id, status, created_at, updated_at)
           VALUES (?, 'idle', ?, ?)""",
        (book_id, now, now),
    )
    await db.commit()
    return {
        "id": cur.lastrowid,
        "book_id": book_id,
        "active_chapter_idx": 0,
        "reading_paragraph_idx": 0,
        "assistant_frontier_chapter_idx": 0,
        "assistant_frontier_paragraph_idx": 0,
        "context_frontier_chapter_idx": 0,
        "context_frontier_paragraph_idx": 0,
        "latest_summary_id": None,
        "live_l2_chunk_ids_json": None,
        "compaction_epoch": 0,
        "status": "idle",
        "running_job_id": None,
        "pending_chapter_idx": None,
        "pending_paragraph_idx": None,
        "pending_scroll_pct": None,
        "pending_assistant_frontier_chapter_idx": None,
        "pending_assistant_frontier_paragraph_idx": None,
        "pending_context_jump_chars": None,
        "pending_updated_at": None,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }


async def update_state(
    db: aiosqlite.Connection,
    book_id: int,
    *,
    active_chapter_idx: int | None = None,
    reading_paragraph_idx: int | None = None,
    assistant_frontier_chapter_idx: int | None = None,
    assistant_frontier_paragraph_idx: int | None = None,
    context_frontier_chapter_idx: int | None = None,
    context_frontier_paragraph_idx: int | None = None,
    latest_summary_id: int | None | object = _UNSET,
    live_l2_chunk_ids: list[int] | None = None,
    compaction_epoch: int | None = None,
    status: str | None = None,
    running_job_id: int | None | object = _UNSET,
    pending_chapter_idx: int | None | object = _UNSET,
    pending_paragraph_idx: int | None | object = _UNSET,
    pending_scroll_pct: float | None | object = _UNSET,
    pending_assistant_frontier_chapter_idx: int | None | object = _UNSET,
    pending_assistant_frontier_paragraph_idx: int | None | object = _UNSET,
    pending_context_jump_chars: int | None | object = _UNSET,
    pending_updated_at: str | None | object = _UNSET,
    emergency_overflow_used: int | None = None,
    last_error: str | None | object = _UNSET,
) -> None:
    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [_now()]

    _MAPPING = {
        "active_chapter_idx": active_chapter_idx,
        "reading_paragraph_idx": reading_paragraph_idx,
        "assistant_frontier_chapter_idx": assistant_frontier_chapter_idx,
        "assistant_frontier_paragraph_idx": assistant_frontier_paragraph_idx,
        "context_frontier_chapter_idx": context_frontier_chapter_idx,
        "context_frontier_paragraph_idx": context_frontier_paragraph_idx,
        "latest_summary_id": latest_summary_id,
        "compaction_epoch": compaction_epoch,
        "status": status,
        "running_job_id": running_job_id,
        "pending_chapter_idx": pending_chapter_idx,
        "pending_paragraph_idx": pending_paragraph_idx,
        "pending_scroll_pct": pending_scroll_pct,
        "pending_assistant_frontier_chapter_idx": pending_assistant_frontier_chapter_idx,
        "pending_assistant_frontier_paragraph_idx": pending_assistant_frontier_paragraph_idx,
        "pending_context_jump_chars": pending_context_jump_chars,
        "pending_updated_at": pending_updated_at,
        "emergency_overflow_used": emergency_overflow_used,
        "last_error": last_error,
    }

    for col, val in _MAPPING.items():
        if val is _UNSET:
            continue
        sets.append(f"{col} = ?")
        params.append(val)

    if live_l2_chunk_ids is not None:
        sets.append("live_l2_chunk_ids_json = ?")
        params.append(json.dumps(live_l2_chunk_ids))

    params.append(book_id)
    await db.execute(
        f"UPDATE book_context_states SET {', '.join(sets)} WHERE book_id = ?",
        params,
    )
    await db.commit()
