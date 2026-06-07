from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from fastapi import APIRouter, File, Path as PathParam, Request, UploadFile

from ..errors import AppError
from ..repos import books as book_repo
from ..repos import progress as progress_repo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["books"])


def _book_summary(
    book: dict[str, Any], progress: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book["author"] if "author" in book.keys() else None,
        "cover_url": f"/api/books/{book['id']}/cover"
        if book.get("cover_path")
        else None,
        "total_chapters": book["total_chapters"],
        "imported_at": book["imported_at"]
        if "imported_at" in book.keys()
        else book.get("created_at", ""),
        "updated_at": book["updated_at"],
        "last_progress": progress,
    }


@router.post("/books/import")
async def import_book(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.endswith(".epub"):
        raise AppError("invalid_epub", "Only .epub files are accepted")

    from .config import current_settings

    settings = current_settings(request)
    db = request.app.state.db

    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        from ..services.import_service import import_epub

        estimator = getattr(request.app.state, "token_estimator", None)
        estimator_info = None
        if estimator is not None:
            estimator_info = estimator.get_calibration_info(
                settings.effective_model_identity("global")
            )

        result = await import_epub(
            db,
            tmp_path,
            settings.books_dir,
            l2_config=settings.context_l2,
            estimator_info=estimator_info,
        )
    finally:
        os.unlink(tmp_path)

    return result


@router.get("/books")
async def list_books(
    request: Request,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    db = request.app.state.db
    books, total = await book_repo.list_books(db, q=q, limit=limit, offset=offset)

    items = []
    for b in books:
        prog = await progress_repo.get_progress(db, b["id"])
        lp = prog if prog.get("updated_at") else None
        items.append(_book_summary(b, lp))

    return {"items": items, "total": total}


@router.get("/books/{book_id}")
async def get_book(request: Request, book_id: int = PathParam(...)) -> dict[str, Any]:
    db = request.app.state.db
    book = await book_repo.get_book(db, book_id)
    if not book:
        raise AppError("book_not_found", "Book not found", details={"book_id": book_id})

    prog = await progress_repo.get_progress(db, book_id)
    lp = prog if prog.get("updated_at") else None
    summary = _book_summary(book, lp)

    from ..repos import paragraphs as paragraph_repo

    _, total_p = await paragraph_repo.list_paragraphs(db, book_id, 0, limit=1)
    from ..repos import chapters as chapter_repo

    chapters = await chapter_repo.list_chapters(db, book_id)
    total_tokens = sum(c["token_estimate"] for c in chapters)
    total_paragraphs = sum(c["paragraph_count"] for c in chapters)

    summary["paragraph_count"] = total_paragraphs
    summary["token_estimate"] = total_tokens
    return summary


@router.get("/books/{book_id}/cover")
async def get_cover(request: Request, book_id: int = PathParam(...)):
    db = request.app.state.db
    book = await book_repo.get_book(db, book_id)
    if not book:
        raise AppError("book_not_found", "Book not found")

    cover_path = book.get("cover_path")
    if not cover_path or not os.path.exists(cover_path):
        raise AppError("book_not_found", "Cover not found")

    from fastapi.responses import FileResponse

    return FileResponse(cover_path)


@router.delete("/books/{book_id}")
async def delete_book(
    request: Request, book_id: int = PathParam(...)
) -> dict[str, Any]:
    db = request.app.state.db
    book = await book_repo.get_book(db, book_id)
    if not book:
        raise AppError("book_not_found", "Book not found")

    deleted = await book_repo.delete_book(db, book_id)
    return {"deleted": deleted, "book_id": book_id}
