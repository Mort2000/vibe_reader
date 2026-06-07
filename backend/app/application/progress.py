from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from ..application.job_handlers import JobSubmitter
from ..domain.models import BookContextState, ReadingWindow
from ..errors import AppError
from ..repos import books as book_repo
from ..repos import chapters as chapter_repo
from ..repos import context_state
from ..repos import jobs as job_repo
from ..repos import paragraphs as paragraph_repo
from ..repos import progress as progress_repo
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

_SCROLL_DEDUP_THRESHOLD = 0.01


async def ensure_window_and_enqueue_comments(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    settings: Any,
    job_submitter: JobSubmitter,
    token_estimator: Any = None,
) -> ReadingWindow:
    window, is_new = await window_service.get_or_create_window(
        db, book_id, chapter_idx, paragraph_idx, settings
    )
    if is_new:
        ctx_result = await build_context(
            db,
            book_id=book_id,
            chapter_idx=chapter_idx,
            reading_pidx=paragraph_idx,
            settings=settings,
            token_estimator=token_estimator,
        )
        if ctx_result.preflight_triggered:
            from ..services.compaction_service import maybe_enqueue_compaction

            await maybe_enqueue_compaction(
                db,
                job_submitter,
                book_id,
                chapter_idx,
                settings,
                preflight_triggered=True,
            )
        await job_submitter.submit_job(
            db,
            "comment_window",
            book_id,
            chapter_idx,
            window_id=window.id,
        )
    return window


@dataclass
class UpdateProgressCommand:
    book_id: int
    chapter_idx: int
    paragraph_idx: int
    scroll_pct: float


@dataclass
class UpdateProgressResult:
    progress: dict[str, Any]
    assistant_frontier_paragraph_idx: int
    current_window: ReadingWindow | None
    jobs: list[dict[str, Any]]


class UpdateProgressUseCase:
    def __init__(
        self,
        db: aiosqlite.Connection,
        settings: Any,
        job_runner: Any,
        token_estimator: Any = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._job_runner = job_runner
        self._token_estimator = token_estimator

    async def execute(self, cmd: UpdateProgressCommand) -> UpdateProgressResult:
        await self._validate_book_chapter(cmd)
        await self._validate_progress_position(cmd)

        current = await progress_repo.get_progress(self._db, cmd.book_id)
        if self._is_deduped(current, cmd):
            return await self._handle_deduped(cmd, current)

        state = await context_state.get_or_create(self._db, cmd.book_id)
        jump_type = detect_jump_type(state, cmd.chapter_idx, cmd.paragraph_idx)

        if jump_type == "backward":
            return await self._handle_backward_jump(cmd, state)

        if jump_type == "forward":
            await self._validate_forward_jump(cmd, state)

        if state.status == "running":
            return await self._handle_agent_busy(cmd, state)

        return await self._handle_normal(cmd, state, jump_type)

    # --- validation ---

    async def _validate_book_chapter(self, cmd: UpdateProgressCommand) -> None:
        book = await book_repo.get_book(self._db, cmd.book_id)
        if not book:
            raise AppError(
                "book_not_found", "Book not found", details={"book_id": cmd.book_id}
            )
        chapter = await chapter_repo.get_chapter(self._db, cmd.book_id, cmd.chapter_idx)
        if not chapter:
            raise AppError(
                "invalid_progress",
                "Chapter not found",
                details={"book_id": cmd.book_id, "chapter_idx": cmd.chapter_idx},
            )

    async def _validate_progress_position(self, cmd: UpdateProgressCommand) -> None:
        last_p = await paragraph_repo.get_last_paragraph_idx(
            self._db, cmd.book_id, cmd.chapter_idx
        )
        if last_p is None:
            raise AppError(
                "invalid_progress",
                "Chapter has no paragraphs",
                details={"book_id": cmd.book_id, "chapter_idx": cmd.chapter_idx},
            )
        if cmd.paragraph_idx > last_p:
            raise AppError(
                "invalid_progress",
                "Paragraph index out of range",
                details={
                    "book_id": cmd.book_id,
                    "chapter_idx": cmd.chapter_idx,
                    "paragraph_idx": cmd.paragraph_idx,
                    "last_paragraph_idx": last_p,
                },
            )
        paragraph = await paragraph_repo.get_paragraph(
            self._db, cmd.book_id, cmd.chapter_idx, cmd.paragraph_idx
        )
        if paragraph is None:
            raise AppError(
                "invalid_progress",
                "Paragraph not found",
                details={
                    "book_id": cmd.book_id,
                    "chapter_idx": cmd.chapter_idx,
                    "paragraph_idx": cmd.paragraph_idx,
                },
            )

    # --- dedup ---

    def _is_deduped(
        self, current: dict[str, Any], cmd: UpdateProgressCommand
    ) -> bool:
        return bool(
            current.get("chapter_idx") == cmd.chapter_idx
            and current.get("paragraph_idx") == cmd.paragraph_idx
            and current.get("updated_at")
            and abs((current.get("scroll_pct") or 0) - cmd.scroll_pct)
            < _SCROLL_DEDUP_THRESHOLD
        )

    async def _handle_deduped(
        self, cmd: UpdateProgressCommand, current: dict[str, Any]
    ) -> UpdateProgressResult:
        frontier, current_window, jobs = await self._resolve_window_and_jobs(cmd)
        logger.info(
            "progress.update.deduped",
            extra={
                "event": "progress.update.deduped",
                "fields": {
                    "book_id": cmd.book_id,
                    "chapter_idx": cmd.chapter_idx,
                    "paragraph_idx": cmd.paragraph_idx,
                    "assistant_frontier": frontier,
                },
            },
        )
        return UpdateProgressResult(
            progress=current,
            assistant_frontier_paragraph_idx=frontier,
            current_window=current_window,
            jobs=jobs,
        )

    # --- jump detection & validation ---

    async def _validate_forward_jump(
        self, cmd: UpdateProgressCommand, state: BookContextState
    ) -> None:
        settings = self._settings
        new_frontier = await compute_assistant_frontier(
            self._db,
            cmd.book_id,
            cmd.chapter_idx,
            cmd.paragraph_idx,
            settings.reader.lookahead_paragraphs,
        )
        jump_chars = await check_forward_jump_chars(
            self._db, cmd.book_id, cmd.chapter_idx, state, new_frontier
        )
        if jump_chars > settings.context.max_context_jump_chars:
            raise AppError(
                "context_jump_too_large",
                "Forward jump exceeds max_context_jump_chars",
                details={
                    "book_id": cmd.book_id,
                    "chapter_idx": cmd.chapter_idx,
                    "jump_chars": jump_chars,
                    "max_context_jump_chars": settings.context.max_context_jump_chars,
                },
            )
        if await self._check_forward_jump_tokens(
            cmd.book_id, cmd.chapter_idx, state, new_frontier
        ):
            raise AppError(
                "context_jump_too_large",
                "Forward jump exceeds max_context_jump_tokens_estimate",
                details={
                    "book_id": cmd.book_id,
                    "chapter_idx": cmd.chapter_idx,
                    "max_context_jump_tokens_estimate": (
                        settings.context.max_context_jump_tokens_estimate
                    ),
                },
            )

    async def _check_forward_jump_tokens(
        self,
        book_id: int,
        chapter_idx: int,
        state: BookContextState,
        new_frontier: int,
    ) -> bool:
        settings = self._settings
        ctx_frontier_p = state.context_frontier_paragraph_idx
        jump_start_p = ctx_frontier_p + 1
        if new_frontier < jump_start_p:
            return False
        paragraphs, _ = await paragraph_repo.list_paragraphs(
            self._db, book_id, state.active_chapter_idx or chapter_idx
        )
        jump_text = "\n".join(
            p["text"]
            for p in paragraphs
            if jump_start_p <= p["paragraph_idx"] <= new_frontier
        )
        estimator = self._token_estimator
        if estimator is None:
            estimator = TokenEstimator(settings.token_estimation)
        jump_token_est = estimator.get_safe_estimate(
            jump_text,
            settings.effective_model_identity("global"),
        )
        return jump_token_est > settings.context.max_context_jump_tokens_estimate

    # --- window & job resolution ---

    async def _resolve_window_and_jobs(
        self, cmd: UpdateProgressCommand
    ) -> tuple[int, ReadingWindow | None, list[dict[str, Any]]]:
        window = await ensure_window_and_enqueue_comments(
            self._db,
            cmd.book_id,
            cmd.chapter_idx,
            cmd.paragraph_idx,
            self._settings,
            self._job_runner,
            self._token_estimator,
        )

        jobs, _ = await job_repo.list_jobs(
            self._db, book_id=cmd.book_id, chapter_idx=cmd.chapter_idx, limit=5
        )
        frontier = window.assistant_frontier_paragraph_idx
        return frontier, window, jobs

    # --- backward jump ---

    async def _handle_backward_jump(
        self, cmd: UpdateProgressCommand, state: BookContextState
    ) -> UpdateProgressResult:
        progress = await progress_repo.upsert_progress(
            self._db,
            cmd.book_id,
            chapter_idx=cmd.chapter_idx,
            paragraph_idx=cmd.paragraph_idx,
            scroll_pct=cmd.scroll_pct,
        )

        await context_state.update_state(
            self._db,
            cmd.book_id,
            active_chapter_idx=cmd.chapter_idx,
            reading_paragraph_idx=cmd.paragraph_idx,
        )

        jobs, _ = await job_repo.list_jobs(
            self._db, book_id=cmd.book_id, chapter_idx=cmd.chapter_idx, limit=5
        )

        frontier = state.assistant_frontier_paragraph_idx
        logger.info(
            "progress.update.backward_jump",
            extra={
                "event": "progress.update.backward_jump",
                "fields": {
                    "book_id": cmd.book_id,
                    "chapter_idx": cmd.chapter_idx,
                    "paragraph_idx": cmd.paragraph_idx,
                },
            },
        )
        return UpdateProgressResult(
            progress=progress,
            assistant_frontier_paragraph_idx=frontier,
            current_window=None,
            jobs=jobs,
        )

    # --- agent busy ---

    async def _handle_agent_busy(
        self, cmd: UpdateProgressCommand, state: BookContextState
    ) -> UpdateProgressResult:
        jump_type = detect_jump_type(state, cmd.chapter_idx, cmd.paragraph_idx)

        has_existing_pending = (
            state.pending_chapter_idx is not None
            and state.pending_paragraph_idx is not None
        )

        # Monotonic pending: never replace a farther legal pending with a closer one.
        if has_existing_pending and not is_reading_at_least(
            cmd.chapter_idx,
            cmd.paragraph_idx,
            ref_chapter_idx=state.pending_chapter_idx,
            ref_paragraph_idx=state.pending_paragraph_idx,
        ):
            jobs, _ = await job_repo.list_jobs(
                self._db, book_id=cmd.book_id, chapter_idx=cmd.chapter_idx, limit=5
            )
            frontier = state.assistant_frontier_paragraph_idx
            progress = await progress_repo.upsert_progress(
                self._db,
                cmd.book_id,
                chapter_idx=cmd.chapter_idx,
                paragraph_idx=cmd.paragraph_idx,
                scroll_pct=cmd.scroll_pct,
            )
            logger.info(
                "progress.update.agent_busy.pending_preserved",
                extra={
                    "event": "progress.update.agent_busy.pending_preserved",
                    "fields": {
                        "book_id": cmd.book_id,
                        "chapter_idx": cmd.chapter_idx,
                        "paragraph_idx": cmd.paragraph_idx,
                        "pending_chapter_idx": state.pending_chapter_idx,
                        "pending_paragraph_idx": state.pending_paragraph_idx,
                    },
                },
            )
            return UpdateProgressResult(
                progress=progress,
                assistant_frontier_paragraph_idx=frontier,
                current_window=None,
                jobs=jobs,
            )

        # Explicit backward relative to last processed position: keep pending.
        if jump_type == "backward" and has_existing_pending:
            jobs, _ = await job_repo.list_jobs(
                self._db, book_id=cmd.book_id, chapter_idx=cmd.chapter_idx, limit=5
            )
            frontier = state.assistant_frontier_paragraph_idx
            return UpdateProgressResult(
                progress=await progress_repo.get_progress(self._db, cmd.book_id),
                assistant_frontier_paragraph_idx=frontier,
                current_window=None,
                jobs=jobs,
            )

        # Validate forward jump doesn't exceed max_context_jump_chars
        if jump_type == "forward":
            new_frontier = await compute_assistant_frontier(
                self._db,
                cmd.book_id,
                cmd.chapter_idx,
                cmd.paragraph_idx,
                self._settings.reader.lookahead_paragraphs,
            )
            jump_chars = await check_forward_jump_chars(
                self._db, cmd.book_id, cmd.chapter_idx, state, new_frontier
            )
            if jump_chars > self._settings.context.max_context_jump_chars:
                jobs, _ = await job_repo.list_jobs(
                    self._db,
                    book_id=cmd.book_id,
                    chapter_idx=cmd.chapter_idx,
                    limit=5,
                )
                frontier = state.assistant_frontier_paragraph_idx
                return UpdateProgressResult(
                    progress=await progress_repo.get_progress(
                        self._db, cmd.book_id
                    ),
                    assistant_frontier_paragraph_idx=frontier,
                    current_window=None,
                    jobs=jobs,
                )
            if await self._check_forward_jump_tokens(
                cmd.book_id, cmd.chapter_idx, state, new_frontier
            ):
                jobs, _ = await job_repo.list_jobs(
                    self._db,
                    book_id=cmd.book_id,
                    chapter_idx=cmd.chapter_idx,
                    limit=5,
                )
                frontier = state.assistant_frontier_paragraph_idx
                return UpdateProgressResult(
                    progress=await progress_repo.get_progress(
                        self._db, cmd.book_id
                    ),
                    assistant_frontier_paragraph_idx=frontier,
                    current_window=None,
                    jobs=jobs,
                )

        # Compute new assistant frontier for the pending position
        new_assistant_frontier = await compute_assistant_frontier(
            self._db,
            cmd.book_id,
            cmd.chapter_idx,
            cmd.paragraph_idx,
            self._settings.reader.lookahead_paragraphs,
        )

        # Compute jump chars for forward jumps (0 for normal)
        pending_jump_chars = 0
        if jump_type == "forward":
            pending_jump_chars = await check_forward_jump_chars(
                self._db, cmd.book_id, cmd.chapter_idx, state, new_assistant_frontier
            )

        progress = await progress_repo.upsert_progress(
            self._db,
            cmd.book_id,
            chapter_idx=cmd.chapter_idx,
            paragraph_idx=cmd.paragraph_idx,
            scroll_pct=cmd.scroll_pct,
        )

        await context_state.update_state(
            self._db,
            cmd.book_id,
            pending_chapter_idx=cmd.chapter_idx,
            pending_paragraph_idx=cmd.paragraph_idx,
            pending_scroll_pct=cmd.scroll_pct,
            pending_assistant_frontier_chapter_idx=cmd.chapter_idx,
            pending_assistant_frontier_paragraph_idx=new_assistant_frontier,
            pending_context_jump_chars=pending_jump_chars,
            pending_updated_at=datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )

        jobs, _ = await job_repo.list_jobs(
            self._db, book_id=cmd.book_id, chapter_idx=cmd.chapter_idx, limit=5
        )

        frontier = state.assistant_frontier_paragraph_idx
        logger.info(
            "progress.update.agent_busy",
            extra={
                "event": "progress.update.agent_busy",
                "fields": {
                    "book_id": cmd.book_id,
                    "chapter_idx": cmd.chapter_idx,
                    "paragraph_idx": cmd.paragraph_idx,
                    "running_job_id": state.running_job_id,
                },
            },
        )
        return UpdateProgressResult(
            progress=progress,
            assistant_frontier_paragraph_idx=frontier,
            current_window=None,
            jobs=jobs,
        )

    # --- normal flow ---

    async def _handle_normal(
        self,
        cmd: UpdateProgressCommand,
        state: BookContextState,
        jump_type: str,
    ) -> UpdateProgressResult:
        progress = await progress_repo.upsert_progress(
            self._db,
            cmd.book_id,
            chapter_idx=cmd.chapter_idx,
            paragraph_idx=cmd.paragraph_idx,
            scroll_pct=cmd.scroll_pct,
        )

        await context_state.update_state(
            self._db,
            cmd.book_id,
            active_chapter_idx=cmd.chapter_idx,
            reading_paragraph_idx=cmd.paragraph_idx,
        )

        frontier, current_window, jobs = await self._resolve_window_and_jobs(cmd)

        await context_state.update_state(
            self._db,
            cmd.book_id,
            assistant_frontier_chapter_idx=cmd.chapter_idx,
            assistant_frontier_paragraph_idx=frontier,
            context_frontier_chapter_idx=cmd.chapter_idx,
            context_frontier_paragraph_idx=frontier,
        )

        logger.info(
            "progress.update.accepted",
            extra={
                "event": "progress.update.accepted",
                "fields": {
                    "book_id": cmd.book_id,
                    "chapter_idx": cmd.chapter_idx,
                    "paragraph_idx": cmd.paragraph_idx,
                    "assistant_frontier": frontier,
                    "jump_type": jump_type,
                },
            },
        )

        return UpdateProgressResult(
            progress=progress,
            assistant_frontier_paragraph_idx=frontier,
            current_window=current_window,
            jobs=jobs,
        )
