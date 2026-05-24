from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Path as PathParam, Request
from pydantic import BaseModel, Field

from ..errors import AppError
from ..repos import context_state
from ..repos import paragraphs as paragraph_repo
from ..repos import progress as progress_repo
from ..repos import chapters as chapter_repo
from ..repos import jobs as job_repo
from ..services import window_service
from ..services.context_builder import build_context
from ..services.progress_helpers import (
    check_forward_jump_chars,
    compute_assistant_frontier,
    detect_jump_type,
    is_reading_at_least,
)
from ..services.token_estimator import TokenEstimator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["progress"])

_SCROLL_DEDUP_THRESHOLD = 0.01


async def _check_forward_jump_tokens(
    db: Any,
    book_id: int,
    chapter_idx: int,
    state: dict[str, Any],
    new_frontier: int,
    settings: Any,
    estimator: Any = None,
) -> bool:
    """Return True if forward jump exceeds max_context_jump_tokens_estimate."""
    ctx_frontier_p = state.get("context_frontier_paragraph_idx", 0)
    jump_start_p = ctx_frontier_p + 1
    if new_frontier < jump_start_p:
        return False
    paragraphs, _ = await paragraph_repo.list_paragraphs(
        db, book_id, state.get("active_chapter_idx", chapter_idx)
    )
    jump_text = "\n".join(
        p["text"]
        for p in paragraphs
        if jump_start_p <= p["paragraph_idx"] <= new_frontier
    )
    if estimator is None:
        estimator = TokenEstimator(settings.token_estimation)
    jump_token_est = estimator.get_safe_estimate(jump_text, settings.llm.model)
    return jump_token_est > settings.context.max_context_jump_tokens_estimate


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


def _detect_jump_type(
    state: dict[str, Any],
    chapter_idx: int,
    paragraph_idx: int,
) -> str:
    return detect_jump_type(state, chapter_idx, paragraph_idx)


async def _check_forward_jump_chars(
    db: Any,
    book_id: int,
    chapter_idx: int,
    state: dict[str, Any],
    new_frontier: int,
    max_chars: int,
) -> int:
    return await check_forward_jump_chars(
        db,
        book_id,
        chapter_idx,
        state,
        new_frontier,
    )


async def _progress_response_fields(
    db: Any,
    request: Request,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    settings: Any,
) -> tuple[int, dict[str, Any] | None, list[dict[str, Any]]]:
    window, is_new = await window_service.get_or_create_window(
        db, book_id, chapter_idx, paragraph_idx, settings
    )

    jobs: list[dict[str, Any]] = []

    if is_new:
        job_runner = request.app.state.job_runner

        estimator = getattr(request.app.state, "token_estimator", None)
        ctx_result = await build_context(
            db,
            book_id=book_id,
            chapter_idx=chapter_idx,
            reading_pidx=paragraph_idx,
            settings=settings,
            token_estimator=estimator,
        )

        if ctx_result.preflight_triggered:
            # Submit compaction first so it runs before comment,
            # ensuring comment uses post-compaction context
            await job_runner.submit_job(db, "compact_context", book_id, chapter_idx)

        await job_runner.submit_job(
            db, "comment_window", book_id, chapter_idx, window_id=window["id"]
        )

    jobs, _ = await job_repo.list_jobs(
        db, book_id=book_id, chapter_idx=chapter_idx, limit=5
    )

    frontier = window["assistant_frontier_paragraph_idx"]
    return frontier, window, jobs


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
    shared_estimator = getattr(request.app.state, "token_estimator", None)

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

    await _validate_progress_position(db, book_id, body.chapter_idx, body.paragraph_idx)

    current = await progress_repo.get_progress(db, book_id)
    if (
        current.get("chapter_idx") == body.chapter_idx
        and current.get("paragraph_idx") == body.paragraph_idx
        and current.get("updated_at")
        and abs((current.get("scroll_pct") or 0) - body.scroll_pct)
        < _SCROLL_DEDUP_THRESHOLD
    ):
        return await _handle_deduped(db, request, book_id, body, settings, current)

    state = await context_state.get_or_create(db, book_id)
    jump_type = _detect_jump_type(state, body.chapter_idx, body.paragraph_idx)

    if jump_type == "backward":
        return await _handle_backward_jump(db, book_id, body, settings, state)

    if jump_type == "forward":
        new_frontier = await compute_assistant_frontier(
            db,
            book_id,
            body.chapter_idx,
            body.paragraph_idx,
            settings.reader.lookahead_paragraphs,
        )
        jump_chars = await _check_forward_jump_chars(
            db,
            book_id,
            body.chapter_idx,
            state,
            new_frontier,
            settings.context.max_context_jump_chars,
        )
        if jump_chars > settings.context.max_context_jump_chars:
            raise AppError(
                "context_jump_too_large",
                "Forward jump exceeds max_context_jump_chars",
                details={
                    "book_id": book_id,
                    "chapter_idx": body.chapter_idx,
                    "jump_chars": jump_chars,
                    "max_context_jump_chars": settings.context.max_context_jump_chars,
                },
            )
        if await _check_forward_jump_tokens(
            db,
            book_id,
            body.chapter_idx,
            state,
            new_frontier,
            settings,
            estimator=shared_estimator,
        ):
            raise AppError(
                "context_jump_too_large",
                "Forward jump exceeds max_context_jump_tokens_estimate",
                details={
                    "book_id": book_id,
                    "chapter_idx": body.chapter_idx,
                    "max_context_jump_tokens_estimate": (
                        settings.context.max_context_jump_tokens_estimate
                    ),
                },
            )

    if state.get("status") == "running":
        return await _handle_agent_busy(
            db, book_id, body, settings, state, shared_estimator
        )

    progress = await progress_repo.upsert_progress(
        db,
        book_id,
        chapter_idx=body.chapter_idx,
        paragraph_idx=body.paragraph_idx,
        scroll_pct=body.scroll_pct,
    )

    await context_state.update_state(
        db,
        book_id,
        active_chapter_idx=body.chapter_idx,
        reading_paragraph_idx=body.paragraph_idx,
    )

    frontier, current_window, jobs = await _progress_response_fields(
        db,
        request,
        book_id,
        body.chapter_idx,
        body.paragraph_idx,
        settings,
    )

    await _update_context_frontier(db, book_id, body, frontier)

    logger.info(
        "progress.update.accepted",
        extra={
            "event": "progress.update.accepted",
            "fields": {
                "book_id": book_id,
                "chapter_idx": body.chapter_idx,
                "paragraph_idx": body.paragraph_idx,
                "assistant_frontier": frontier,
                "jump_type": jump_type,
            },
        },
    )

    return {
        "progress": progress,
        "assistant_frontier_paragraph_idx": frontier,
        "current_window": current_window,
        "jobs": jobs,
    }


async def _handle_deduped(
    db: Any,
    request: Request,
    book_id: int,
    body: ProgressRequest,
    settings: Any,
    current: dict[str, Any],
) -> dict[str, Any]:
    frontier, current_window, jobs = await _progress_response_fields(
        db,
        request,
        book_id,
        body.chapter_idx,
        body.paragraph_idx,
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


async def _handle_backward_jump(
    db: Any,
    book_id: int,
    body: ProgressRequest,
    settings: Any,
    state: dict[str, Any],
) -> dict[str, Any]:
    progress = await progress_repo.upsert_progress(
        db,
        book_id,
        chapter_idx=body.chapter_idx,
        paragraph_idx=body.paragraph_idx,
        scroll_pct=body.scroll_pct,
    )

    await context_state.update_state(
        db,
        book_id,
        active_chapter_idx=body.chapter_idx,
        reading_paragraph_idx=body.paragraph_idx,
    )

    jobs, _ = await job_repo.list_jobs(
        db, book_id=book_id, chapter_idx=body.chapter_idx, limit=5
    )

    frontier = state.get("assistant_frontier_paragraph_idx", 0)
    logger.info(
        "progress.update.backward_jump",
        extra={
            "event": "progress.update.backward_jump",
            "fields": {
                "book_id": book_id,
                "chapter_idx": body.chapter_idx,
                "paragraph_idx": body.paragraph_idx,
            },
        },
    )
    return {
        "progress": progress,
        "assistant_frontier_paragraph_idx": frontier,
        "current_window": None,
        "jobs": jobs,
    }


async def _handle_agent_busy(
    db: Any,
    book_id: int,
    body: ProgressRequest,
    settings: Any,
    state: dict[str, Any],
    shared_estimator: Any = None,
) -> dict[str, Any]:
    jump_type = _detect_jump_type(state, body.chapter_idx, body.paragraph_idx)

    has_existing_pending = (
        state.get("pending_chapter_idx") is not None
        and state.get("pending_paragraph_idx") is not None
    )

    # Monotonic pending: never replace a farther legal pending with a closer one.
    if has_existing_pending and not is_reading_at_least(
        body.chapter_idx,
        body.paragraph_idx,
        ref_chapter_idx=int(state["pending_chapter_idx"]),
        ref_paragraph_idx=int(state["pending_paragraph_idx"]),
    ):
        jobs, _ = await job_repo.list_jobs(
            db, book_id=book_id, chapter_idx=body.chapter_idx, limit=5
        )
        frontier = state.get("assistant_frontier_paragraph_idx", 0)
        progress = await progress_repo.upsert_progress(
            db,
            book_id,
            chapter_idx=body.chapter_idx,
            paragraph_idx=body.paragraph_idx,
            scroll_pct=body.scroll_pct,
        )
        logger.info(
            "progress.update.agent_busy.pending_preserved",
            extra={
                "event": "progress.update.agent_busy.pending_preserved",
                "fields": {
                    "book_id": book_id,
                    "chapter_idx": body.chapter_idx,
                    "paragraph_idx": body.paragraph_idx,
                    "pending_chapter_idx": state.get("pending_chapter_idx"),
                    "pending_paragraph_idx": state.get("pending_paragraph_idx"),
                },
            },
        )
        return {
            "progress": progress,
            "assistant_frontier_paragraph_idx": frontier,
            "current_window": None,
            "jobs": jobs,
        }

    # Explicit backward relative to last processed position: keep pending.
    if jump_type == "backward" and has_existing_pending:
        jobs, _ = await job_repo.list_jobs(
            db, book_id=book_id, chapter_idx=body.chapter_idx, limit=5
        )
        frontier = state.get("assistant_frontier_paragraph_idx", 0)
        return {
            "progress": await progress_repo.get_progress(db, book_id),
            "assistant_frontier_paragraph_idx": frontier,
            "current_window": None,
            "jobs": jobs,
        }

    # Validate forward jump doesn't exceed max_context_jump_chars
    if jump_type == "forward":
        new_frontier = await compute_assistant_frontier(
            db,
            book_id,
            body.chapter_idx,
            body.paragraph_idx,
            settings.reader.lookahead_paragraphs,
        )
        jump_chars = await _check_forward_jump_chars(
            db,
            book_id,
            body.chapter_idx,
            state,
            new_frontier,
            settings.context.max_context_jump_chars,
        )
        if jump_chars > settings.context.max_context_jump_chars:
            jobs, _ = await job_repo.list_jobs(
                db, book_id=book_id, chapter_idx=body.chapter_idx, limit=5
            )
            frontier = state.get("assistant_frontier_paragraph_idx", 0)
            return {
                "progress": await progress_repo.get_progress(db, book_id),
                "assistant_frontier_paragraph_idx": frontier,
                "current_window": None,
                "jobs": jobs,
            }
        if await _check_forward_jump_tokens(
            db,
            book_id,
            body.chapter_idx,
            state,
            new_frontier,
            settings,
            estimator=shared_estimator,
        ):
            jobs, _ = await job_repo.list_jobs(
                db, book_id=book_id, chapter_idx=body.chapter_idx, limit=5
            )
            frontier = state.get("assistant_frontier_paragraph_idx", 0)
            return {
                "progress": await progress_repo.get_progress(db, book_id),
                "assistant_frontier_paragraph_idx": frontier,
                "current_window": None,
                "jobs": jobs,
            }

    # Compute new assistant frontier for the pending position
    new_assistant_frontier = await compute_assistant_frontier(
        db,
        book_id,
        body.chapter_idx,
        body.paragraph_idx,
        settings.reader.lookahead_paragraphs,
    )

    # Compute jump chars for forward jumps (0 for normal)
    pending_jump_chars = 0
    if jump_type == "forward":
        pending_jump_chars = await _check_forward_jump_chars(
            db,
            book_id,
            body.chapter_idx,
            state,
            new_assistant_frontier,
            settings.context.max_context_jump_chars,
        )

    from datetime import datetime, timezone

    progress = await progress_repo.upsert_progress(
        db,
        book_id,
        chapter_idx=body.chapter_idx,
        paragraph_idx=body.paragraph_idx,
        scroll_pct=body.scroll_pct,
    )

    await context_state.update_state(
        db,
        book_id,
        pending_chapter_idx=body.chapter_idx,
        pending_paragraph_idx=body.paragraph_idx,
        pending_scroll_pct=body.scroll_pct,
        pending_assistant_frontier_chapter_idx=body.chapter_idx,
        pending_assistant_frontier_paragraph_idx=new_assistant_frontier,
        pending_context_jump_chars=pending_jump_chars,
        pending_updated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    jobs, _ = await job_repo.list_jobs(
        db, book_id=book_id, chapter_idx=body.chapter_idx, limit=5
    )

    frontier = state.get("assistant_frontier_paragraph_idx", 0)
    logger.info(
        "progress.update.agent_busy",
        extra={
            "event": "progress.update.agent_busy",
            "fields": {
                "book_id": book_id,
                "chapter_idx": body.chapter_idx,
                "paragraph_idx": body.paragraph_idx,
                "running_job_id": state.get("running_job_id"),
            },
        },
    )
    return {
        "progress": progress,
        "assistant_frontier_paragraph_idx": frontier,
        "current_window": None,
        "jobs": jobs,
    }


async def _update_context_frontier(
    db: Any,
    book_id: int,
    body: ProgressRequest,
    frontier: int,
) -> None:
    await context_state.update_state(
        db,
        book_id,
        assistant_frontier_chapter_idx=body.chapter_idx,
        assistant_frontier_paragraph_idx=frontier,
        context_frontier_chapter_idx=body.chapter_idx,
        context_frontier_paragraph_idx=frontier,
    )
