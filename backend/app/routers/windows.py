from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Path as PathParam, Query, Request
from pydantic import BaseModel, Field

from ..errors import AppError
from ..repos import comments as comment_repo
from ..repos import jobs as job_repo
from ..repos import windows as window_repo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["windows"])


class RetryRequest(BaseModel):
    reason: str = Field("manual_retry", min_length=1)


@router.get("/books/{book_id}/chapters/{chapter_idx}/windows/current")
async def get_current_window(
    request: Request,
    book_id: int = PathParam(...),
    chapter_idx: int = PathParam(...),
    paragraph_idx: int | None = Query(None),
) -> dict[str, Any]:
    db = request.app.state.db

    window = await window_repo.find_latest_window(db, book_id, chapter_idx)
    if window is None:
        raise AppError(
            "window_not_found",
            "No window found for this chapter",
            details={"book_id": book_id, "chapter_idx": chapter_idx},
        )

    focus_start = window["focus_start_paragraph_idx"]
    focus_end = window["focus_end_paragraph_idx"]
    comments, total = await comment_repo.list_comments(
        db, book_id, chapter_idx, start=focus_start, end=focus_end
    )

    return {
        "window": window,
        "comments_ready_count": len(comments),
        "comments_target_count": focus_end - focus_start + 1,
    }


@router.get("/books/{book_id}/chapters/{chapter_idx}/comments")
async def list_chapter_comments(
    request: Request,
    book_id: int = PathParam(...),
    chapter_idx: int = PathParam(...),
    start: int | None = Query(None),
    end: int | None = Query(None),
    status: str = Query("active"),
    limit: int | None = Query(None),
) -> dict[str, Any]:
    db = request.app.state.db

    comments, total = await comment_repo.list_comments(
        db,
        book_id,
        chapter_idx,
        start=start,
        end=end,
        status=status,
        limit=limit,
    )
    return {"items": comments, "total": total}


@router.post("/windows/{window_id}/retry")
async def retry_window(
    request: Request,
    window_id: int = PathParam(...),
    body: RetryRequest | None = None,
) -> dict[str, Any]:
    db = request.app.state.db

    window = await window_repo.get_window(db, window_id)
    if window is None:
        raise AppError(
            "window_not_found",
            "Window not found",
            details={"window_id": window_id},
        )

    jobs, _ = await job_repo.list_jobs(
        db,
        book_id=window["book_id"],
        chapter_idx=window["chapter_idx"],
        job_type="comment_window",
        status="running",
        limit=10,
    )
    running_for_window = [j for j in jobs if j.get("window_id") == window_id]
    if running_for_window:
        raise AppError(
            "job_already_running",
            "A comment job is already running for this window",
            details={"window_id": window_id, "job_id": running_for_window[0]["id"]},
        )

    failed_jobs, _ = await job_repo.list_jobs(
        db,
        book_id=window["book_id"],
        chapter_idx=window["chapter_idx"],
        job_type="comment_window",
        status="failed",
        limit=10,
    )
    failed_for_window = [j for j in failed_jobs if j.get("window_id") == window_id]

    job_runner = request.app.state.job_runner

    if failed_for_window:
        job = await job_runner.retry_job(db, failed_for_window[0]["id"])
    else:
        job = await job_runner.submit_job(
            db,
            "comment_window",
            window["book_id"],
            window["chapter_idx"],
            window_id=window_id,
        )

    return {"window": window, "job": job}
