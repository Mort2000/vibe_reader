from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def create_job(
    db: aiosqlite.Connection,
    *,
    job_type: str,
    book_id: int,
    chapter_idx: int,
    window_id: int | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    now = _now()
    cur = await db.execute(
        """INSERT INTO ai_jobs
           (job_type, book_id, chapter_idx, window_id, status, attempt_count, trace_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
        (job_type, book_id, chapter_idx, window_id, trace_id, now, now),
    )
    await db.commit()
    return {
        "id": cur.lastrowid,
        "job_type": job_type,
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "window_id": window_id,
        "status": "pending",
        "attempt_count": 0,
        "error": None,
        "trace_id": trace_id,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
    }


async def get_job(db: aiosqlite.Connection, job_id: int) -> dict[str, Any] | None:
    cur = await db.execute("SELECT * FROM ai_jobs WHERE id = ?", (job_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def update_job_status(
    db: aiosqlite.Connection,
    job_id: int,
    status: str,
    *,
    error: str | None = None,
    trace_id: str | None = None,
) -> None:
    now = _now()
    started_at = now if status == "running" else None
    completed_at = now if status in ("done", "failed", "skipped") else None

    sets = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, now]

    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if trace_id is not None:
        sets.append("trace_id = ?")
        params.append(trace_id)
    if started_at:
        sets.append("started_at = ?")
        params.append(started_at)
    if completed_at:
        sets.append("completed_at = ?")
        params.append(completed_at)

    params.append(job_id)
    await db.execute(f"UPDATE ai_jobs SET {', '.join(sets)} WHERE id = ?", params)
    await db.commit()


async def increment_attempt(db: aiosqlite.Connection, job_id: int) -> None:
    await db.execute(
        "UPDATE ai_jobs SET attempt_count = attempt_count + 1, updated_at = ? WHERE id = ?",
        (_now(), job_id),
    )
    await db.commit()


async def list_jobs(
    db: aiosqlite.Connection,
    *,
    book_id: int | None = None,
    chapter_idx: int | None = None,
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    """List AI jobs."""
    conditions: list[str] = []
    params: list[Any] = []

    if book_id is not None:
        conditions.append("ai_jobs.book_id = ?")
        params.append(book_id)
    if chapter_idx is not None:
        conditions.append("ai_jobs.chapter_idx = ?")
        params.append(chapter_idx)
    if status is not None:
        conditions.append("ai_jobs.status = ?")
        params.append(status)
    if job_type is not None:
        conditions.append("ai_jobs.job_type = ?")
        params.append(job_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    table = "ai_jobs"

    count_cur = await db.execute(f"SELECT COUNT(*) FROM {table} {where}", params)
    total = (await count_cur.fetchone())[0]

    cur = await db.execute(
        f"SELECT ai_jobs.* FROM {table} {where} "
        f"ORDER BY ai_jobs.created_at DESC LIMIT ?",
        params + [limit],
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows], total
