from __future__ import annotations

import logging
from typing import Any

import aiosqlite

from ..application.job_handlers import JobSubmitter
from ..domain.models import BookContextState
from ..repos import context_state
from ..repos import paragraphs as paragraph_repo
from ..repos import progress as progress_repo
from ..services.progress_helpers import (
    compute_assistant_frontier,
    validate_pending_progress,
)
from ..services.token_estimator import TokenEstimator
from .progress import ensure_window_and_enqueue_comments

logger = logging.getLogger(__name__)


class PendingProgressProcessor:
    def __init__(self, token_estimator: Any = None) -> None:
        self._token_estimator = token_estimator

    async def process(
        self,
        db: aiosqlite.Connection,
        book_id: int,
        state: BookContextState,
        settings: Any,
        job_submitter: JobSubmitter,
    ) -> None:
        chapter_idx = state.pending_chapter_idx
        paragraph_idx = state.pending_paragraph_idx
        scroll_pct = state.pending_scroll_pct
        pending_af_ch = state.pending_assistant_frontier_chapter_idx
        pending_af_p = state.pending_assistant_frontier_paragraph_idx
        pending_jump_chars = state.pending_context_jump_chars

        _clear_pending = {
            "status": "idle",
            "running_job_id": None,
            "pending_chapter_idx": None,
            "pending_paragraph_idx": None,
            "pending_scroll_pct": None,
            "pending_assistant_frontier_chapter_idx": None,
            "pending_assistant_frontier_paragraph_idx": None,
            "pending_context_jump_chars": None,
            "pending_updated_at": None,
        }

        if chapter_idx is None or paragraph_idx is None:
            await context_state.update_state(db, book_id, **_clear_pending)
            return

        jump_type, jump_chars = await validate_pending_progress(
            db,
            book_id,
            state,
            chapter_idx=chapter_idx,
            paragraph_idx=paragraph_idx,
            assistant_frontier_chapter_idx=pending_af_ch,
            assistant_frontier_paragraph_idx=pending_af_p,
            max_context_jump_chars=settings.context.max_context_jump_chars,
            lookahead_paragraphs=settings.reader.lookahead_paragraphs,
        )

        if jump_type in ("backward", "forward_rejected"):
            logger.info(
                "job_runner.pending_discarded",
                extra={
                    "event": "job_runner.pending_discarded",
                    "fields": {
                        "book_id": book_id,
                        "chapter_idx": chapter_idx,
                        "paragraph_idx": paragraph_idx,
                        "jump_type": jump_type,
                        "jump_chars": jump_chars,
                        "stored_jump_chars": pending_jump_chars,
                    },
                },
            )
            await context_state.update_state(db, book_id, **_clear_pending)
            return

        if jump_type == "forward_accepted":
            ctx_frontier_p = state.context_frontier_paragraph_idx
            jump_start_p = ctx_frontier_p + 1
            jump_end_p = pending_af_p if pending_af_p else paragraph_idx

            if jump_end_p >= jump_start_p:
                jump_paragraphs, _ = await paragraph_repo.list_paragraphs(
                    db,
                    book_id,
                    state.active_chapter_idx,
                )
                jump_text = "\n".join(
                    p["text"]
                    for p in jump_paragraphs
                    if jump_start_p <= p["paragraph_idx"] <= jump_end_p
                )
                estimator = self._token_estimator
                if estimator is None:
                    estimator = TokenEstimator(settings.token_estimation)
                jump_token_est = estimator.get_safe_estimate(
                    jump_text,
                    settings.effective_model_identity("global"),
                )
                max_jump_tokens = settings.context.max_context_jump_tokens_estimate
                if jump_token_est > max_jump_tokens:
                    logger.info(
                        "job_runner.pending_token_jump_rejected",
                        extra={
                            "event": "job_runner.pending_token_jump_rejected",
                            "fields": {
                                "book_id": book_id,
                                "jump_token_estimate": jump_token_est,
                                "max_jump_tokens": max_jump_tokens,
                            },
                        },
                    )
                    await context_state.update_state(db, book_id, **_clear_pending)
                    return

        assistant_frontier = pending_af_p
        if assistant_frontier is None:
            assistant_frontier = await compute_assistant_frontier(
                db,
                book_id,
                chapter_idx,
                paragraph_idx,
                settings.reader.lookahead_paragraphs,
            )

        await progress_repo.upsert_progress(
            db,
            book_id,
            chapter_idx=chapter_idx,
            paragraph_idx=paragraph_idx,
            scroll_pct=scroll_pct or 0.0,
        )

        await context_state.update_state(
            db,
            book_id,
            active_chapter_idx=chapter_idx,
            reading_paragraph_idx=paragraph_idx,
        )

        await ensure_window_and_enqueue_comments(
            db,
            book_id,
            chapter_idx,
            paragraph_idx,
            settings,
            job_submitter,
            self._token_estimator,
        )

        await context_state.update_state(
            db,
            book_id,
            assistant_frontier_chapter_idx=chapter_idx,
            assistant_frontier_paragraph_idx=assistant_frontier,
            context_frontier_chapter_idx=chapter_idx,
            context_frontier_paragraph_idx=assistant_frontier,
            **_clear_pending,
        )

        logger.info(
            "job_runner.pending_processed",
            extra={
                "event": "job_runner.pending_processed",
                "fields": {
                    "book_id": book_id,
                    "chapter_idx": chapter_idx,
                    "paragraph_idx": paragraph_idx,
                    "assistant_frontier": assistant_frontier,
                    "jump_type": jump_type,
                    "jump_chars": jump_chars,
                },
            },
        )
