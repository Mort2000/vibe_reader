from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path as PathParam, Request
from pydantic import BaseModel, Field

from ..application.progress import UpdateProgressCommand, UpdateProgressUseCase
from ..repos import progress as progress_repo

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
    job_runner = request.app.state.job_runner
    token_estimator = getattr(request.app.state, "token_estimator", None)

    use_case = UpdateProgressUseCase(
        db=db,
        settings=settings,
        job_runner=job_runner,
        token_estimator=token_estimator,
    )
    result = await use_case.execute(
        UpdateProgressCommand(
            book_id=book_id,
            chapter_idx=body.chapter_idx,
            paragraph_idx=body.paragraph_idx,
            scroll_pct=body.scroll_pct,
        )
    )
    return {
        "progress": result.progress,
        "assistant_frontier_paragraph_idx": result.assistant_frontier_paragraph_idx,
        "current_window": result.current_window,
        "jobs": result.jobs,
    }
