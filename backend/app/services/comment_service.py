from __future__ import annotations

import logging
import time
from typing import Any

import aiosqlite
from pydantic_ai.messages import ModelResponse, ToolCallPart

from ..config import Settings
from ..domain.models import ReadingWindow
from ..application.agent_run_result import AgentRunResult, CommentAuditContext
from ..observability import (
    ensure_trace_id,
    mark_span_error,
    record_agent_metric,
    set_span_attributes,
    start_observable_span,
)
from ..repos import books as book_repo
from ..repos import chapters as chapter_repo
from ..repos import chunks as chunk_repo
from ..repos import comments as comment_repo
from ..repos import context_state
from ..repos import paragraphs as paragraph_repo
from .agent_base import (
    CommentDensityHint,
    CommentDeps,
    EmitCommentDraft,
    get_comment_agent,
)
from .context_builder import build_context


def _paragraphs_with_evidence(
    target_paragraphs: list[int],
    ctx_result: Any,
) -> list[int]:
    """Return subset of target_paragraphs that have original text in live chunks."""
    if not ctx_result.live_chunk_ids and ctx_result.partial_chunk_id is None:
        return []

    live_ranges: list[tuple[int, int]] = []
    partial_frontier = ctx_result.partial_frontier_paragraph_idx

    for chunk in getattr(ctx_result, "_live_chunks_detail", []):
        start = chunk.start_paragraph_idx
        end = chunk.end_paragraph_idx
        if chunk.id == ctx_result.partial_chunk_id and partial_frontier is not None:
            end = partial_frontier
        live_ranges.append((start, end))

    if not live_ranges:
        return []

    evidence: list[int] = []
    for pidx in target_paragraphs:
        for start, end in live_ranges:
            if start <= pidx <= end:
                evidence.append(pidx)
                break
    return evidence


def _no_evidence_telemetry(
    *,
    job_id: int,
    window_id: int,
    target_set: set[int],
    ctx_result: Any,
    density_hint: CommentDensityHint,
    missing_count: int,
) -> AgentRunResult:
    return AgentRunResult(
        agent_name="ParagraphCommentAgent",
        invocation_id="",
        duration_ms=0,
        no_call=True,
        context_hash=ctx_result.context_hash,
        context_estimated_tokens=ctx_result.estimated_tokens,
        preflight_triggered=ctx_result.preflight_triggered,
        hard_triggered=ctx_result.hard_triggered,
        context_degraded=True,
        missing_target_original_count=missing_count,
        prompt_manifest=ctx_result.prompt_manifest,
        comment_density_actual=density_hint.current_density,
        comment_density_soft_min=density_hint.soft_min_density,
        density_stat_start=density_hint.stat_start_paragraph_idx,
        density_stat_end=density_hint.stat_end_paragraph_idx,
        candidate_lookup_count=len(target_set),
    )


logger = logging.getLogger(__name__)


def _usage_scope_from_messages(messages: list[Any]) -> str:
    response_rounds = 0
    has_tool_call = False
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        response_rounds += 1
        if any(isinstance(part, ToolCallPart) for part in message.parts):
            has_tool_call = True
    if response_rounds > 1 or has_tool_call:
        return "run_aggregate"
    return "single_request"


async def _has_running_compaction(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
) -> bool:
    from ..repos import jobs as job_repo

    jobs, _ = await job_repo.list_jobs(
        db,
        book_id=book_id,
        chapter_idx=chapter_idx,
        job_type="compact_context",
        status="running",
        limit=1,
    )
    return bool(jobs)


async def _wait_for_running_compaction(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    *,
    timeout_s: float,
    poll_interval_s: float = 0.25,
) -> bool:
    """Wait for a running compact_context job to finish.

    Pending compaction jobs are not polled: this handler runs under the
    per-book lock, so a pending compaction cannot start until we release it.
    """
    import asyncio

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not await _has_running_compaction(db, book_id, chapter_idx):
            return True
        await asyncio.sleep(poll_interval_s)
    return False


def _build_density_hint(
    active_count: int,
    stat_start: int,
    stat_end: int,
    total_target_paragraphs: int,
    soft_min: float,
) -> CommentDensityHint:
    current_density = active_count / max(1, total_target_paragraphs)
    estimated_missing = max(0, int(total_target_paragraphs * soft_min) - active_count)
    return CommentDensityHint(
        stat_start_paragraph_idx=stat_start,
        stat_end_paragraph_idx=stat_end,
        stat_target_paragraph_count=total_target_paragraphs,
        active_comment_count=active_count,
        soft_min_density=soft_min,
        current_density=round(current_density, 4),
        estimated_missing_comments=estimated_missing,
    )


def _validate_and_dedupe(
    raw_payloads: list[dict[str, Any]],
    target_set: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], int]:
    seen: set[int] = set()
    valid: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    discarded_by_reason: dict[str, int] = {}
    validation_failed_count = 0

    def discard(payload: dict[str, Any], reason: str) -> None:
        discarded.append({"payload": payload, "reason": reason})
        discarded_by_reason[reason] = discarded_by_reason.get(reason, 0) + 1

    for payload in raw_payloads:
        try:
            draft = EmitCommentDraft.model_validate(payload)
        except Exception:
            validation_failed_count += 1
            discard(payload, "validation_failed")
            continue

        if draft.paragraph_idx not in target_set:
            discard(payload, "out_of_target")
            continue
        if not draft.comment.strip():
            discard(payload, "empty_comment")
            continue
        if draft.paragraph_idx in seen:
            discard(payload, "duplicate_paragraph")
            continue
        seen.add(draft.paragraph_idx)
        valid.append(
            {
                "paragraph_idx": draft.paragraph_idx,
                "comment": draft.comment.strip(),
                "comment_type": draft.comment_type,
            }
        )

    return valid, discarded, discarded_by_reason, validation_failed_count


async def _build_comment_context(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    chapter_idx: int,
    reading_pidx: int,
    settings: Settings,
    focus_start: int,
    focus_end: int,
    target_paragraphs: list[int],
    density_hint: CommentDensityHint,
    book_title: str | None,
    chapter_title: str | None,
    overflow_used: bool,
    token_estimator: Any = None,
) -> tuple[Any, str, bool]:
    compaction_cleared = True
    if await _has_running_compaction(db, book_id, chapter_idx):
        compaction_cleared = await _wait_for_running_compaction(
            db,
            book_id,
            chapter_idx,
            timeout_s=settings.context_l3.compaction_timeout_s,
        )

    build_kwargs = dict(
        book_id=book_id,
        chapter_idx=chapter_idx,
        reading_pidx=reading_pidx,
        settings=settings,
        task_type="comment",
        focus_start=focus_start,
        focus_end=focus_end,
        target_paragraphs=target_paragraphs,
        density_hint=density_hint,
        book_title=book_title,
        chapter_title=chapter_title,
        overflow_already_used=overflow_used,
        token_estimator=token_estimator,
    )

    ctx_result = await build_context(db, **build_kwargs)

    context_degraded = ctx_result.context_degraded
    if ctx_result.hard_triggered and not compaction_cleared:
        context_degraded = True

    return ctx_result, ctx_result.prompt, context_degraded


async def _run_comment_llm(
    db: aiosqlite.Connection,
    book_id: int,
    chapter_idx: int,
    window_id: int,
    target_set: set[int],
    density_hint: CommentDensityHint,
    prompt: str,
    settings: Settings,
) -> tuple[Any, CommentDeps, float, str]:
    deps = CommentDeps(
        target_paragraph_ids=target_set,
        density_hint=density_hint,
    )

    agent = get_comment_agent(settings)
    llm = settings.effective_llm("comment")
    trace_id = ensure_trace_id()

    t0 = time.monotonic()
    span_attrs = {
        "ai.agent": "ParagraphCommentAgent",
        "ai.model": llm.model,
        "book.id": book_id,
        "chapter.idx": chapter_idx,
        "window.id": window_id,
        "app.trace_id": trace_id,
    }
    with start_observable_span("ai.ParagraphCommentAgent.run", span_attrs) as span:
        try:
            result = await agent.run(
                prompt,
                deps=deps,
                metadata={
                    "book_id": book_id,
                    "chapter_idx": chapter_idx,
                    "window_id": window_id,
                    "trace_id": trace_id,
                },
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            mark_span_error(span, exc)
            record_agent_metric(
                agent="ParagraphCommentAgent",
                model=llm.model,
                status="error",
                duration_ms=latency_ms,
            )
            raise
        latency_ms = (time.monotonic() - t0) * 1000
        usage = result.usage()
        usage_input = (
            usage.request_tokens
            if usage.request_tokens is not None
            else usage.input_tokens
        )
        usage_output = (
            usage.response_tokens
            if usage.response_tokens is not None
            else usage.output_tokens
        )
        set_span_attributes(
            span,
            {
                "ai.input_tokens": usage_input,
                "ai.output_tokens": usage_output,
                "ai.cached_input_tokens": usage.cache_read_tokens,
                "duration_ms": round(latency_ms, 2),
            },
        )
        record_agent_metric(
            agent="ParagraphCommentAgent",
            model=llm.model,
            status="ok",
            duration_ms=latency_ms,
            input_tokens=usage_input,
            output_tokens=usage_output,
            cached_input_tokens=usage.cache_read_tokens or None,
        )

    return result, deps, latency_ms, trace_id


async def _persist_valid_comments(
    db: aiosqlite.Connection,
    valid_comments: list[dict[str, Any]],
    book_id: int,
    chapter_idx: int,
    window_id: int,
    trace_id: str,
) -> list[dict[str, Any]]:
    persisted: list[dict[str, Any]] = []
    for c in valid_comments:
        created = await comment_repo.create_comment(
            db,
            book_id=book_id,
            chapter_idx=chapter_idx,
            paragraph_idx=c["paragraph_idx"],
            window_id=window_id,
            comment=c["comment"],
            comment_type=c["comment_type"],
            trace_id=trace_id,
        )
        persisted.append({**c, "comment_id": created.get("id"), "trace_id": trace_id})
    return persisted


async def _enrich_with_live_chunks(
    db: aiosqlite.Connection,
    ctx_result: Any,
    book_id: int,
    chapter_idx: int,
) -> None:
    live_chunks = await chunk_repo.list_chunks(
        db, book_id, chapter_idx, status="active"
    )
    ctx_result._live_chunks_detail = [
        c
        for c in live_chunks
        if c.id in ctx_result.live_chunk_ids
        or c.id == ctx_result.partial_chunk_id
    ]


async def run_comment_task(
    db: aiosqlite.Connection,
    job_id: int,
    window: ReadingWindow | None,
    settings: Settings,
    token_estimator: Any = None,
) -> AgentRunResult:
    if window is None:
        raise ValueError(f"Window not found for job {job_id}")

    window_id = window.id
    book_id = window.book_id
    chapter_idx = window.chapter_idx

    focus_start = window.focus_start_paragraph_idx
    focus_end = window.focus_end_paragraph_idx
    start_pidx = window.start_paragraph_idx
    frontier = window.assistant_frontier_paragraph_idx

    target_paragraphs = list(range(focus_start, focus_end + 1))
    target_set = set(target_paragraphs)

    window_paragraphs = await paragraph_repo.get_paragraphs_range(
        db,
        book_id,
        chapter_idx,
        start_pidx,
        window.end_paragraph_idx,
    )

    book = await book_repo.get_book(db, book_id)
    chapter = await chapter_repo.get_chapter(db, book_id, chapter_idx)
    if not book or not chapter:
        raise ValueError(f"Book/chapter not found: {book_id}/{chapter_idx}")

    wc = settings.window_l1
    stat_start = max(0, frontier - wc.comment_density_stat_window_paragraphs)
    stat_end = frontier

    active_count = await comment_repo.count_active_comments_in_range(
        db, book_id, chapter_idx, stat_start, stat_end
    )

    total_in_range = await paragraph_repo.get_paragraphs_range(
        db, book_id, chapter_idx, stat_start, stat_end
    )
    stat_target_count = len(total_in_range)

    density_hint = _build_density_hint(
        active_count=active_count,
        stat_start=stat_start,
        stat_end=stat_end,
        total_target_paragraphs=stat_target_count,
        soft_min=wc.comment_density_soft_min,
    )

    # Use the window's frontier to derive the reading position, ensuring
    # build_context computes a frontier >= the window's own frontier.
    # start_pidx includes overlap and is earlier than the reading position,
    # which would cause build_context to underestimate the frontier.
    reading_pidx = max(
        start_pidx,
        frontier - settings.reader.lookahead_paragraphs,
    )

    book_state = await context_state.get_or_create(db, book_id)
    overflow_used = bool(book_state.emergency_overflow_used)

    ctx_result, prompt, context_degraded = await _build_comment_context(
        db,
        book_id=book_id,
        chapter_idx=chapter_idx,
        reading_pidx=reading_pidx,
        settings=settings,
        focus_start=focus_start,
        focus_end=focus_end,
        target_paragraphs=target_paragraphs,
        density_hint=density_hint,
        book_title=book.get("title"),
        chapter_title=chapter.get("title"),
        overflow_used=overflow_used,
        token_estimator=token_estimator,
    )

    await _enrich_with_live_chunks(db, ctx_result, book_id, chapter_idx)
    evidenced_targets = _paragraphs_with_evidence(target_paragraphs, ctx_result)
    missing_count = len(target_paragraphs) - len(evidenced_targets)

    if not evidenced_targets:
        logger.warning(
            "comment_task.no_evidence_no_call",
            extra={
                "event": "comment_task.no_evidence_no_call",
                "fields": {
                    "job_id": job_id,
                    "window_id": window_id,
                    "missing_target_original_count": missing_count,
                    "context_degraded": True,
                },
            },
        )
        return _no_evidence_telemetry(
            job_id=job_id,
            window_id=window_id,
            target_set=target_set,
            ctx_result=ctx_result,
            density_hint=density_hint,
            missing_count=missing_count,
        )

    if missing_count > 0:
        target_paragraphs = evidenced_targets
        target_set = set(evidenced_targets)
        logger.warning(
            "comment_task.partial_evidence",
            extra={
                "event": "comment_task.partial_evidence",
                "fields": {
                    "job_id": job_id,
                    "window_id": window_id,
                    "missing_target_original_count": missing_count,
                    "remaining_targets": len(evidenced_targets),
                },
            },
        )

    if ctx_result.emergency_overflow_used and not overflow_used:
        await context_state.update_state(
            db,
            book_id,
            emergency_overflow_used=1,
        )

    if ctx_result.preflight_triggered:
        logger.info(
            "comment_task.preflight_compaction",
            extra={
                "event": "comment_task.preflight_compaction",
                "fields": {
                    "job_id": job_id,
                    "estimated_tokens": ctx_result.estimated_tokens,
                },
            },
        )

    if context_degraded:
        logger.warning(
            "comment_task.context_degraded",
            extra={
                "event": "comment_task.context_degraded",
                "fields": {
                    "job_id": job_id,
                    "hard_triggered": ctx_result.hard_triggered,
                    "estimated_tokens": ctx_result.estimated_tokens,
                },
            },
        )

    await comment_repo.delete_comments_by_window(db, window_id)

    result, deps, latency_ms, trace_id = await _run_comment_llm(
        db,
        book_id,
        chapter_idx,
        window_id,
        target_set,
        density_hint,
        prompt,
        settings,
    )

    raw_payloads = deps.raw_tool_payloads
    valid_comments, discarded, discarded_by_reason, validation_failed_count = (
        _validate_and_dedupe(raw_payloads, target_set)
    )

    no_call = len(raw_payloads) == 0

    persisted_comments = await _persist_valid_comments(
        db,
        valid_comments,
        book_id,
        chapter_idx,
        window_id,
        trace_id,
    )

    usage = result.usage()
    usage_scope = _usage_scope_from_messages(result.all_messages())

    log_fields: dict[str, Any] = {
        "job_id": job_id,
        "window_id": window_id,
        "target_count": len(target_set),
        "tool_call_count": len(raw_payloads),
        "valid_count": len(valid_comments),
        "validation_failed_count": validation_failed_count,
        "discarded_count": len(discarded),
        "discarded_by_reason": discarded_by_reason,
        "no_call": no_call,
        "latency_ms": round(latency_ms, 1),
        "request_tokens": usage.request_tokens,
        "response_tokens": usage.response_tokens,
        "total_tokens": (usage.request_tokens or 0) + (usage.response_tokens or 0),
        "comment_density_actual": density_hint.current_density,
        "comment_density_soft_min": density_hint.soft_min_density,
        "comment_density_stat_start": density_hint.stat_start_paragraph_idx,
        "comment_density_stat_end": density_hint.stat_end_paragraph_idx,
        "context_estimated_tokens": ctx_result.estimated_tokens,
        "context_hash": ctx_result.context_hash,
    }

    logger.info(
        "comment_task.completed",
        extra={
            "event": "comment_task.completed",
            "fields": log_fields,
        },
    )

    if discarded:
        logger.warning(
            "comment_task.discarded_comments",
            extra={
                "event": "comment_task.discarded_comments",
                "fields": {
                    "window_id": window_id,
                    "trace_id": trace_id,
                    "discarded_count": len(discarded),
                    "discarded_by_reason": discarded_by_reason,
                },
            },
        )

    # PydanticAI RunUsage exposes both legacy (request/response_tokens) and
    # current (input/output_tokens) names; prefer legacy when set.
    usage_input = (
        usage.request_tokens if usage.request_tokens is not None else usage.input_tokens
    )
    usage_output = (
        usage.response_tokens
        if usage.response_tokens is not None
        else usage.output_tokens
    )
    usage_source = "provider" if usage_input is not None else "estimate"

    return AgentRunResult(
        agent_name="ParagraphCommentAgent",
        duration_ms=round(latency_ms, 1),
        prompt_version="comment_v1",
        input_tokens=usage_input,
        output_tokens=usage_output,
        cached_input_tokens=usage.cache_read_tokens or None,
        no_call=no_call,
        tool_call_count=len(raw_payloads),
        valid_count=len(valid_comments),
        validation_failed_count=validation_failed_count,
        discarded_count=len(discarded),
        discarded_by_reason=discarded_by_reason,
        candidate_lookup_count=len(target_set),
        context_hash=ctx_result.context_hash,
        comment_density_actual=density_hint.current_density,
        comment_density_soft_min=density_hint.soft_min_density,
        density_stat_start=density_hint.stat_start_paragraph_idx,
        density_stat_end=density_hint.stat_end_paragraph_idx,
        context_estimated_tokens=ctx_result.estimated_tokens,
        preflight_triggered=ctx_result.preflight_triggered,
        hard_triggered=ctx_result.hard_triggered,
        context_degraded=context_degraded,
        usage_scope=usage_scope,
        prompt_manifest=ctx_result.prompt_manifest,
        audit_context=CommentAuditContext(
            trace_id=trace_id,
            book=book,
            chapter_idx=chapter_idx,
            window=window,
            window_paragraphs=window_paragraphs,
            target_paragraphs=target_paragraphs,
            density_hint=density_hint,
            prompt=prompt,
            agent_result=result,
            raw_payloads=raw_payloads,
            valid_comments=persisted_comments,
            discarded=discarded,
            validation_failed_count=validation_failed_count,
            no_call=no_call,
            usage_source=usage_source,
            context_manifest=ctx_result.prompt_manifest,
        ),
    )
