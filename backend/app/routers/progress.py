from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Path as PathParam, Request
from pydantic import BaseModel, Field

from ..errors import AppError
from ..repos import paragraphs as paragraph_repo
from ..repos import progress as progress_repo
from ..repos import windows as window_repo
from ..repos import chapters as chapter_repo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["progress"])


class ProgressRequest(BaseModel):
    chapter_idx: int = Field(..., ge=0)
    paragraph_idx: int = Field(..., ge=0)
    scroll_pct: float = Field(..., ge=0.0, le=1.0)


@router.get("/books/{book_id}/progress")
async def get_progress(
    request: Request,
    book_id: int = PathParam(...),
) -> dict[str, Any]:
    db = request.app.state.db
    return await progress_repo.get_progress(db, book_id)


@router.put("/books/{book_id}/progress")
async def update_progress(
    request: Request,
    book_id: int = PathParam(...),
    body: ProgressRequest = ...,
) -> dict[str, Any]:
    db = request.app.state.db
    settings = request.app.state.settings

    from ..repos import books as book_repo
    book = await book_repo.get_book(db, book_id)
    if not book:
        raise AppError("book_not_found", "Book not found", details={"book_id": book_id})

    chapter = await chapter_repo.get_chapter(db, book_id, body.chapter_idx)
    if not chapter:
        raise AppError("invalid_progress", "Chapter not found", details={"book_id": book_id, "chapter_idx": body.chapter_idx})

    progress = await progress_repo.upsert_progress(
        db,
        book_id,
        chapter_idx=body.chapter_idx,
        paragraph_idx=body.paragraph_idx,
        scroll_pct=body.scroll_pct,
    )

    last_p = await paragraph_repo.get_last_paragraph_idx(db, book_id, body.chapter_idx)
    frontier = min(
        body.paragraph_idx + settings.reader.lookahead_paragraphs,
        last_p if last_p is not None else body.paragraph_idx,
    )

    latest_window = await window_repo.find_latest_window(db, book_id, body.chapter_idx)

    current_window: dict[str, Any] | None = None
    jobs: list[dict[str, Any]] = []

    if latest_window:
        current_window = latest_window

    logger.info(
        "progress.update.accepted",
        extra={
            "event": "progress.update.accepted",
            "fields": {
                "book_id": book_id,
                "chapter_idx": body.chapter_idx,
                "paragraph_idx": body.paragraph_idx,
                "assistant_frontier": frontier,
            },
        },
    )

    return {
        "progress": progress,
        "assistant_frontier_paragraph_idx": frontier,
        "current_window": current_window,
        "jobs": jobs,
    }
