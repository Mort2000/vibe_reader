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

_SCROLL_DEDUP_THRESHOLD = 0.01


class ProgressRequest(BaseModel):
    chapter_idx: int = Field(..., ge=0)
    paragraph_idx: int = Field(..., ge=0)
    scroll_pct: float = Field(..., ge=0.0, le=1.0)


async def _validate_progress_position(
    db: Any,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
) -> int:
    """Ensure the paragraph exists; return last_paragraph_idx for the chapter."""
    last_p = await paragraph_repo.get_last_paragraph_idx(db, book_id, chapter_idx)
    if last_p is None:
        raise AppError(
            "invalid_progress",
            "Chapter has no paragraphs",
            details={"book_id": book_id, "chapter_idx": chapter_idx},
        )
    if paragraph_idx > last_p:
        raise AppError(
            "invalid_progress",
            "Paragraph index out of range",
            details={
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "paragraph_idx": paragraph_idx,
                "last_paragraph_idx": last_p,
            },
        )
    paragraph = await paragraph_repo.get_paragraph(
        db, book_id, chapter_idx, paragraph_idx
    )
    if paragraph is None:
        raise AppError(
            "invalid_progress",
            "Paragraph not found",
            details={
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "paragraph_idx": paragraph_idx,
            },
        )
    return last_p


async def _progress_response_fields(
    db: Any,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    last_p: int,
    settings: Any,
) -> tuple[int, dict[str, Any] | None, list[dict[str, Any]]]:
    frontier = min(
        paragraph_idx + settings.reader.lookahead_paragraphs,
        last_p,
    )
    latest_window = await window_repo.find_latest_window(db, book_id, chapter_idx)
    current_window: dict[str, Any] | None = latest_window if latest_window else None
    return frontier, current_window, []


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
        raise AppError(
            "invalid_progress",
            "Chapter not found",
            details={"book_id": book_id, "chapter_idx": body.chapter_idx},
        )

    last_p = await _validate_progress_position(
        db, book_id, body.chapter_idx, body.paragraph_idx
    )

    current = await progress_repo.get_progress(db, book_id)
    if (
        current.get("chapter_idx") == body.chapter_idx
        and current.get("paragraph_idx") == body.paragraph_idx
        and current.get("updated_at")
        and abs((current.get("scroll_pct") or 0) - body.scroll_pct)
        < _SCROLL_DEDUP_THRESHOLD
    ):
        frontier, current_window, jobs = await _progress_response_fields(
            db,
            book_id,
            body.chapter_idx,
            body.paragraph_idx,
            last_p,
            settings,
        )
        logger.info(
            "progress.update.deduped",
            extra={
                "event": "progress.update.deduped",
                "fields": {
                    "book_id": book_id,
                    "chapter_idx": body.chapter_idx,
                    "paragraph_idx": body.paragraph_idx,
                    "assistant_frontier": frontier,
                },
            },
        )
        return {
            "progress": current,
            "assistant_frontier_paragraph_idx": frontier,
            "current_window": current_window,
            "jobs": jobs,
        }

    progress = await progress_repo.upsert_progress(
        db,
        book_id,
        chapter_idx=body.chapter_idx,
        paragraph_idx=body.paragraph_idx,
        scroll_pct=body.scroll_pct,
    )

    frontier, current_window, jobs = await _progress_response_fields(
        db,
        book_id,
        body.chapter_idx,
        body.paragraph_idx,
        last_p,
        settings,
    )

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
