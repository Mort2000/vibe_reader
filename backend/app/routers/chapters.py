from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path as PathParam, Query, Request

from ..errors import AppError
from ..repos import chapters as chapter_repo
from ..repos import comments as comment_repo
from ..repos import paragraphs as paragraph_repo

router = APIRouter(tags=["chapters"])


@router.get("/books/{book_id}/chapters")
async def list_chapters(
    request: Request,
    book_id: int = PathParam(...),
) -> dict[str, Any]:
    db = request.app.state.db
    chapters = await chapter_repo.list_chapters(db, book_id)
    items = [
        {
            "book_id": c["book_id"],
            "idx": c["idx"],
            "title": c["title"],
            "paragraph_count": c["paragraph_count"] if "paragraph_count" in c.keys() else 0,
            "token_estimate": c["token_estimate"] if "token_estimate" in c.keys() else 0,
        }
        for c in chapters
    ]
    return {"items": items, "total": len(items)}


@router.get("/books/{book_id}/chapters/{chapter_idx}")
async def get_chapter(
    request: Request,
    book_id: int = PathParam(...),
    chapter_idx: int = PathParam(...),
) -> dict[str, Any]:
    db = request.app.state.db
    chapter = await chapter_repo.get_chapter(db, book_id, chapter_idx)
    if not chapter:
        raise AppError("chapter_not_found", "Chapter not found", details={"book_id": book_id, "chapter_idx": chapter_idx})

    all_chapters = await chapter_repo.list_chapters(db, book_id)
    prev_idx = None
    next_idx = None
    for i, c in enumerate(all_chapters):
        if c["idx"] == chapter_idx:
            if i > 0:
                prev_idx = all_chapters[i - 1]["idx"]
            if i < len(all_chapters) - 1:
                next_idx = all_chapters[i + 1]["idx"]
            break

    def _get(d: dict, key: str, default: Any = 0) -> Any:
        return d[key] if key in d.keys() else default

    return {
        "book_id": book_id,
        "idx": chapter_idx,
        "title": chapter["title"],
        "paragraph_count": _get(chapter, "paragraph_count"),
        "token_estimate": _get(chapter, "token_estimate"),
        "prev_chapter_idx": prev_idx,
        "next_chapter_idx": next_idx,
    }


@router.get("/books/{book_id}/chapters/{chapter_idx}/paragraphs")
async def list_paragraphs(
    request: Request,
    book_id: int = PathParam(...),
    chapter_idx: int = PathParam(...),
    start: int = Query(0),
    limit: int | None = Query(None),
    include_comments: bool = Query(False),
) -> dict[str, Any]:
    db = request.app.state.db
    paragraphs, total = await paragraph_repo.list_paragraphs(
        db, book_id, chapter_idx, start=start, limit=limit
    )

    comments_map: dict[int, list[dict[str, Any]]] = {}
    if include_comments:
        comments, _ = await comment_repo.list_comments(db, book_id, chapter_idx, status="active")
        for c in comments:
            pidx = c["paragraph_idx"]
            comments_map.setdefault(pidx, []).append(c)

    items = []
    for p in paragraphs:
        item: dict[str, Any] = {
            "book_id": p["book_id"],
            "chapter_idx": p["chapter_idx"],
            "paragraph_idx": p["paragraph_idx"],
            "text": p["text"],
        }
        if include_comments:
            item["comments"] = comments_map.get(p["paragraph_idx"], [])
        items.append(item)

    return {"book_id": book_id, "chapter_idx": chapter_idx, "items": items, "total": total}
