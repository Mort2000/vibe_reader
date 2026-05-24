from __future__ import annotations

import json
from typing import Any

import aiosqlite

from ..domain.models import ChapterCompressedSummary


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def create_summary(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    chapter_idx: int,
    covered_start_paragraph_idx: int,
    covered_end_paragraph_idx: int,
    source_chunk_ids: list[int],
    source_text_hash: str,
    summary: str,
    anchor_excerpts: list[dict[str, Any]],
    token_estimate: int,
    context_version: int = 1,
    compaction_epoch: int = 0,
    auto_commit: bool = True,
) -> ChapterCompressedSummary:
    now = _now()
    cur = await db.execute(
        """INSERT INTO chapter_compressed_summaries
           (book_id, chapter_idx, covered_start_paragraph_idx, covered_end_paragraph_idx,
            source_chunk_ids_json, source_text_hash, summary, anchor_excerpts_json,
            token_estimate, context_version, compaction_epoch, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            book_id,
            chapter_idx,
            covered_start_paragraph_idx,
            covered_end_paragraph_idx,
            json.dumps(source_chunk_ids),
            source_text_hash,
            summary,
            json.dumps(anchor_excerpts, ensure_ascii=False),
            token_estimate,
            context_version,
            compaction_epoch,
            now,
        ),
    )
    if auto_commit:
        await db.commit()
    return ChapterCompressedSummary(
        id=cur.lastrowid,
        book_id=book_id,
        chapter_idx=chapter_idx,
        covered_start_paragraph_idx=covered_start_paragraph_idx,
        covered_end_paragraph_idx=covered_end_paragraph_idx,
        source_chunk_ids=source_chunk_ids,
        source_text_hash=source_text_hash,
        summary=summary,
        anchor_excerpts=anchor_excerpts,
        token_estimate=token_estimate,
        context_version=context_version,
        compaction_epoch=compaction_epoch,
        created_at=now,
    )


async def get_latest_summary(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    frontier_pidx: int | None = None,
) -> ChapterCompressedSummary | None:
    sql = """SELECT * FROM chapter_compressed_summaries
             WHERE book_id = ? AND chapter_idx = ?"""
    params: list[Any] = [book_id, chapter_idx]
    if frontier_pidx is not None:
        sql += " AND covered_end_paragraph_idx <= ?"
        params.append(frontier_pidx)
    sql += " ORDER BY compaction_epoch DESC, created_at DESC LIMIT 1"
    cur = await db.execute(sql, params)
    row = await cur.fetchone()
    return ChapterCompressedSummary.from_row(dict(row)) if row else None


async def get_summary(
    db: aiosqlite.Connection, summary_id: int
) -> ChapterCompressedSummary | None:
    cur = await db.execute(
        "SELECT * FROM chapter_compressed_summaries WHERE id = ?",
        (summary_id,),
    )
    row = await cur.fetchone()
    return ChapterCompressedSummary.from_row(dict(row)) if row else None
