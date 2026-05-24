from __future__ import annotations

import hashlib
from typing import Any

import aiosqlite

from . import paragraphs as paragraph_repo


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def create_chunks_for_chapter(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    target_tokens: int = 24_000,
    min_tokens: int = 18_000,
    max_tokens: int = 32_000,
    max_chunk_chars: int = 8_000,
    max_chunk_paragraphs: int = 180,
    estimator_model: str = "",
    estimator_version: str = "",
    estimator_calibration_ratio: float = 1.0,
    chunking_version: str = "",
) -> list[dict[str, Any]]:
    paragraphs, _ = await paragraph_repo.list_paragraphs(db, book_id, chapter_idx)
    if not paragraphs:
        return []

    await db.execute(
        "DELETE FROM original_text_chunks WHERE book_id = ? AND chapter_idx = ?",
        (book_id, chapter_idx),
    )
    await db.commit()

    chunks: list[dict[str, Any]] = []
    chunk_seq = 0
    buf: list[dict[str, Any]] = []
    buf_tokens = 0
    buf_chars = 0
    buf_paragraphs = 0

    estimator_meta = {
        "estimator_model": estimator_model,
        "estimator_version": estimator_version,
        "estimator_calibration_ratio": estimator_calibration_ratio,
        "chunking_version": chunking_version,
    }

    for p in paragraphs:
        p_tokens = p.get("token_estimate", 0)
        p_chars = p.get("char_count", len(p.get("text", "")))
        should_flush = (
            (buf and buf_tokens + p_tokens > max_tokens and buf_tokens >= min_tokens)
            or (
                buf
                and buf_chars + p_chars > max_chunk_chars
                and buf_tokens >= min_tokens
            )
            or (
                buf
                and buf_paragraphs + 1 > max_chunk_paragraphs
                and buf_tokens >= min_tokens
            )
        )
        if should_flush:
            chunks.append(
                await _insert_chunk(
                    db,
                    book_id,
                    chapter_idx,
                    chunk_seq,
                    buf,
                    **estimator_meta,
                )
            )
            chunk_seq += 1
            buf = []
            buf_tokens = 0
            buf_chars = 0
            buf_paragraphs = 0
        buf.append(p)
        buf_tokens += p_tokens
        buf_chars += p_chars
        buf_paragraphs += 1

    if buf:
        if chunks and buf_tokens < min_tokens:
            last = chunks.pop()
            all_paragraphs = []
            seen: set[int] = set()
            for p in last["_paragraphs"] + buf:
                if p["paragraph_idx"] not in seen:
                    all_paragraphs.append(p)
                    seen.add(p["paragraph_idx"])
            all_paragraphs.sort(key=lambda p: p["paragraph_idx"])
            await db.execute(
                "DELETE FROM original_text_chunks WHERE id = ?", (last["id"],)
            )
            await db.commit()
            chunks.append(
                await _insert_chunk(
                    db,
                    book_id,
                    chapter_idx,
                    last["chunk_seq"],
                    all_paragraphs,
                    **estimator_meta,
                )
            )
        else:
            chunks.append(
                await _insert_chunk(
                    db,
                    book_id,
                    chapter_idx,
                    chunk_seq,
                    buf,
                    **estimator_meta,
                )
            )

    return chunks


async def _insert_chunk(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    chunk_seq: int,
    paragraphs: list[dict[str, Any]],
    *,
    estimator_model: str = "",
    estimator_version: str = "",
    estimator_calibration_ratio: float = 1.0,
    chunking_version: str = "",
) -> dict[str, Any]:
    now = _now()
    start_pidx = paragraphs[0]["paragraph_idx"]
    end_pidx = paragraphs[-1]["paragraph_idx"]
    token_estimate = sum(p.get("token_estimate", 0) for p in paragraphs)
    char_count = sum(p.get("char_count", len(p.get("text", ""))) for p in paragraphs)
    combined = "".join(p.get("text", "") for p in paragraphs)
    text_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]

    cur = await db.execute(
        """INSERT INTO original_text_chunks
           (book_id, chapter_idx, chunk_seq, start_paragraph_idx, end_paragraph_idx,
            token_estimate, char_count, text_hash,
            raw_token_estimate, estimator_model, estimator_version,
            estimator_calibration_ratio, chunking_version,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (
            book_id,
            chapter_idx,
            chunk_seq,
            start_pidx,
            end_pidx,
            token_estimate,
            char_count,
            text_hash,
            token_estimate,
            estimator_model,
            estimator_version,
            estimator_calibration_ratio,
            chunking_version,
            now,
            now,
        ),
    )
    await db.commit()
    return {
        "id": cur.lastrowid,
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "chunk_seq": chunk_seq,
        "start_paragraph_idx": start_pidx,
        "end_paragraph_idx": end_pidx,
        "token_estimate": token_estimate,
        "char_count": char_count,
        "text_hash": text_hash,
        "raw_token_estimate": token_estimate,
        "estimator_model": estimator_model,
        "estimator_version": estimator_version,
        "estimator_calibration_ratio": estimator_calibration_ratio,
        "chunking_version": chunking_version,
        "status": "active",
        "reclaimed_by_summary_id": None,
        "created_at": now,
        "updated_at": now,
        "_paragraphs": paragraphs,
    }


async def list_chunks(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM original_text_chunks WHERE book_id = ? AND chapter_idx = ?"
    params: list[Any] = [book_id, chapter_idx]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY chunk_seq"
    cur = await db.execute(sql, params)
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_chunks_intersecting(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    start_pidx: int,
    end_pidx: int,
) -> list[dict[str, Any]]:
    cur = await db.execute(
        """SELECT * FROM original_text_chunks
           WHERE book_id = ? AND chapter_idx = ?
             AND status = 'active'
             AND start_paragraph_idx <= ?
             AND end_paragraph_idx >= ?
           ORDER BY chunk_seq""",
        (book_id, chapter_idx, end_pidx, start_pidx),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_earliest_complete_unreclaimed(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    frontier_pidx: int,
) -> dict[str, Any] | None:
    cur = await db.execute(
        """SELECT * FROM original_text_chunks
           WHERE book_id = ? AND chapter_idx = ?
             AND status = 'active'
             AND end_paragraph_idx <= ?
           ORDER BY chunk_seq
           LIMIT 1""",
        (book_id, chapter_idx, frontier_pidx),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def count_completed_unreclaimed(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    frontier_pidx: int,
) -> int:
    cur = await db.execute(
        """SELECT COUNT(*) FROM original_text_chunks
           WHERE book_id = ? AND chapter_idx = ?
             AND status = 'active'
             AND end_paragraph_idx <= ?""",
        (book_id, chapter_idx, frontier_pidx),
    )
    row = await cur.fetchone()
    return row[0] if row else 0


async def mark_reclaimed(
    db: aiosqlite.Connection,
    chunk_id: int,
    summary_id: int,
    *,
    auto_commit: bool = True,
) -> None:
    now = _now()
    await db.execute(
        """UPDATE original_text_chunks
           SET status = 'reclaimed', reclaimed_by_summary_id = ?, updated_at = ?
           WHERE id = ?""",
        (summary_id, now, chunk_id),
    )
    if auto_commit:
        await db.commit()


async def get_live_original_tokens(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    frontier: int | None = None,
) -> int:
    if frontier is not None:
        # Full chunks (end <= frontier) + partial chunks that intersect frontier
        cur = await db.execute(
            """SELECT token_estimate, start_paragraph_idx, end_paragraph_idx
               FROM original_text_chunks
               WHERE book_id = ? AND chapter_idx = ? AND status = 'active'
               AND start_paragraph_idx <= ?""",
            (book_id, chapter_idx, frontier),
        )
        rows = await cur.fetchall()
        total = 0
        for r in rows:
            tokens = r[0]
            start_p = r[1]
            end_p = r[2]
            if end_p <= frontier:
                total += tokens
            else:
                # Partial chunk: prorate by paragraph range
                span = max(1, end_p - start_p + 1)
                visible = max(1, frontier - start_p + 1)
                total += int(tokens * visible / span)
        return total
    cur = await db.execute(
        """SELECT COALESCE(SUM(token_estimate), 0) FROM original_text_chunks
           WHERE book_id = ? AND chapter_idx = ? AND status = 'active'""",
        (book_id, chapter_idx),
    )
    row = await cur.fetchone()
    return row[0] if row else 0


async def select_eligible_compaction_source(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    frontier_pidx: int,
    *,
    min_live_chunks_after_compaction: int = 2,
    preferred_live_chunks_after_compaction: int = 3,
    context_pressure: bool = False,
) -> dict[str, Any] | None:
    all_active = await list_chunks(db, book_id, chapter_idx, status="active")
    if not all_active:
        return None

    total_active = len(all_active)

    if total_active <= 1:
        return None

    complete_unreclaimed = [
        c for c in all_active if c["end_paragraph_idx"] <= frontier_pidx
    ]

    if not complete_unreclaimed:
        return None

    remaining = total_active - 1

    if remaining < min_live_chunks_after_compaction:
        return None

    # Three-tier: below preferred, only compact under explicit pressure.
    if remaining < preferred_live_chunks_after_compaction and not context_pressure:
        return None

    return complete_unreclaimed[0]


async def get_chunk(db: aiosqlite.Connection, chunk_id: int) -> dict[str, Any] | None:
    cur = await db.execute(
        "SELECT * FROM original_text_chunks WHERE id = ?", (chunk_id,)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def backfill_missing_chunks(
    db: aiosqlite.Connection,
    target_tokens: int = 24_000,
    min_tokens: int = 18_000,
    max_tokens: int = 32_000,
    max_chunk_chars: int = 8_000,
    max_chunk_paragraphs: int = 180,
    estimator_model: str = "local_v1",
    estimator_version: str = "local_v1",
    estimator_calibration_ratio: float = 1.0,
    chunking_version: str = "v1",
) -> int:
    cur = await db.execute("SELECT id FROM books")
    book_rows = await cur.fetchall()
    backfilled = 0
    for book_row in book_rows:
        book_id = book_row[0]
        ch_cur = await db.execute(
            "SELECT idx FROM chapters WHERE book_id = ?", (book_id,)
        )
        ch_rows = await ch_cur.fetchall()
        for ch_row in ch_rows:
            chapter_idx = ch_row[0]
            cnt_cur = await db.execute(
                "SELECT COUNT(*) FROM original_text_chunks "
                "WHERE book_id = ? AND chapter_idx = ?",
                (book_id, chapter_idx),
            )
            cnt_row = await cnt_cur.fetchone()
            if cnt_row[0] == 0:
                await create_chunks_for_chapter(
                    db,
                    book_id,
                    chapter_idx,
                    target_tokens=target_tokens,
                    min_tokens=min_tokens,
                    max_tokens=max_tokens,
                    max_chunk_chars=max_chunk_chars,
                    max_chunk_paragraphs=max_chunk_paragraphs,
                    estimator_model=estimator_model,
                    estimator_version=estimator_version,
                    estimator_calibration_ratio=estimator_calibration_ratio,
                    chunking_version=chunking_version,
                )
                backfilled += 1
    return backfilled
