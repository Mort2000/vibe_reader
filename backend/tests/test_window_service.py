from __future__ import annotations

import hashlib

import pytest

from app.config import ReaderConfig, Settings, WindowL1Config
from app.db import init_db
from app.repos import paragraphs as paragraph_repo
from app.repos import windows as window_repo
from app.services import window_service


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def _seed_chapter(db, *, paragraph_count: int = 522) -> None:
    now = "2026-05-30T00:00:00Z"
    await db.execute(
        """INSERT INTO books
           (id, title, author, file_hash, file_path, total_chapters, imported_at, updated_at)
           VALUES (1, 'book', 'author', 'hash', '/tmp/book.epub', 1, ?, ?)""",
        (now, now),
    )
    await db.execute(
        """INSERT INTO chapters
           (book_id, idx, title, raw_text, paragraph_count, token_estimate, created_at, updated_at)
           VALUES (1, 1, 'chapter', '', ?, ?, ?, ?)""",
        (paragraph_count, paragraph_count, now, now),
    )
    await paragraph_repo.bulk_insert_paragraphs(
        db,
        1,
        1,
        [
            {
                "paragraph_idx": idx,
                "text": f"paragraph {idx}",
                "text_hash": _hash_text(f"paragraph {idx}"),
                "char_count": 12,
                "token_estimate": 1,
            }
            for idx in range(paragraph_count)
        ],
    )


def _verify_window_settings() -> Settings:
    return Settings(
        reader=ReaderConfig(lookahead_paragraphs=80),
        window_l1=WindowL1Config(
            focus_max_tokens=24_000,
            min_focus_paragraphs=80,
            max_focus_paragraphs=200,
            overlap_paragraphs=4,
            trigger_advance_ratio=0.88,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reading_pidx", "latest_start", "latest_end", "latest_focus_start", "latest_focus_end"),
    [
        (521, 436, 521, 440, 521),
        (431, 436, 521, 440, 521),
    ],
)
async def test_window_not_recreated_when_latest_focus_already_covers_frontier(
    tmp_path,
    reading_pidx: int,
    latest_start: int,
    latest_end: int,
    latest_focus_start: int,
    latest_focus_end: int,
) -> None:
    db = await init_db(tmp_path / "test.db")
    try:
        await _seed_chapter(db)
        latest_paragraphs = await paragraph_repo.get_paragraphs_range(
            db, 1, 1, latest_start, latest_end
        )
        latest = await window_repo.create_window(
            db,
            book_id=1,
            chapter_idx=1,
            window_seq=4,
            start_paragraph_idx=latest_start,
            end_paragraph_idx=latest_end,
            focus_start_paragraph_idx=latest_focus_start,
            focus_end_paragraph_idx=latest_focus_end,
            assistant_frontier_paragraph_idx=latest_focus_end,
            text_hash=window_service.compute_text_hash(latest_paragraphs),
        )
        await window_repo.update_window_status(db, latest.id, "done")

        window, is_new = await window_service.get_or_create_window(
            db,
            book_id=1,
            chapter_idx=1,
            reading_pidx=reading_pidx,
            settings=_verify_window_settings(),
        )

        assert is_new is False
        assert window.id == latest.id
    finally:
        await db.close()
