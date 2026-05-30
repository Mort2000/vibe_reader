"""Comment window waiting, validation, and comment assertions."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..core.client_factory import TargetClient
from ..core.config import (
    READING_STOP_COMMENT_WINDOWS,
    READING_STOP_CROSS_CHAPTER,
    VerifyConfig,
)
from ..core.context import ScenarioContext
from ..assertions.api_contracts import (
    validate_comments_response,
    validate_no_span_in_comments,
    validate_progress_response,
    validate_window_response,
)
from ..metrics_collector import MetricsAggregator
from ..assertions.comments import (
    assert_comment_ids_stable,
    assert_comments_valid,
    assert_no_comment_recreated_events,
    collect_validation_failures,
    progress_update_was_deduped,
    raise_window_failed,
    window_covers_paragraph,
    window_is_no_call,
)
from ..core.scenario import StepAssertionError, assert_that
from ..sse_collector import SSEEvent

from .audit import fetch_verify_jobs
from .corpus import chapter_by_idx, last_paragraph_idx
from .metrics import record_comment_metrics
from .reading import (
    ReadingCursor,
    ReadingSession,
    ReadingTrace,
    advance_reading,
    advance_start_chapter_sync_then_cross,
)

logger = logging.getLogger(__name__)


async def drain_chapter_comment_jobs(
    client: TargetClient,
    session: ReadingSession,
    book_id: int,
    chapter_idx: int,
    anchor_paragraph_idx: int,
    trace: ReadingTrace,
    *,
    scenario_id: str,
    step_id: str,
    config: VerifyConfig,
    timeout_s: float | None = None,
) -> None:
    """Wait until all comment_window jobs for *chapter_idx* finish."""
    max_wait = float(config.params.max_wait_comment_window_s)
    if timeout_s is None:
        timeout_s = max_wait * 40
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        jobs = await fetch_verify_jobs(
            client,
            book_id,
            chapter_idx,
            scenario_id=scenario_id,
            step_id=step_id,
            job_type="comment_window",
        )
        active = [j for j in jobs if j.get("status") in ("pending", "running")]
        if not active:
            return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await wait_for_window_done(
            client,
            session,
            book_id,
            chapter_idx,
            anchor_paragraph_idx,
            min(max_wait, remaining),
            trace,
            retry_on_failure=True,
        )

    jobs = await fetch_verify_jobs(
        client,
        book_id,
        chapter_idx,
        scenario_id=scenario_id,
        step_id=step_id,
        job_type="comment_window",
    )
    active = [j for j in jobs if j.get("status") in ("pending", "running")]
    if active:
        raise StepAssertionError(
            assertion="comment_jobs_drained",
            message=(
                f"Timed out waiting for {len(active)} comment_window jobs "
                f"on chapter {chapter_idx}"
            ),
            actual={
                "chapter_idx": chapter_idx,
                "pending_or_running": len(active),
            },
        )

async def _maybe_retry_failed_window(
    client: TargetClient,
    window: dict[str, Any],
    *,
    retry_on_failure: bool,
) -> bool:
    window_id = window.get("id")
    if not retry_on_failure or window_id is None:
        return False
    await client.retry_window(int(window_id), reason="verify_window_retry")
    return True


async def wait_for_window_done(  # noqa: C901
    client: TargetClient,
    session: ReadingSession,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    timeout_s: float,
    trace: ReadingTrace,
    *,
    retry_on_failure: bool = False,
) -> dict[str, Any] | None:
    """Wait for the window covering paragraph_idx to reach a terminal state."""

    def _chapter_filter(evt: SSEEvent) -> bool:
        return evt.book_id == book_id and evt.chapter_idx == chapter_idx

    async def _handle_failed_window(window: dict[str, Any]) -> bool:
        if await _maybe_retry_failed_window(
            client, window, retry_on_failure=retry_on_failure
        ):
            return True
        raise_window_failed(window)

    deadline = time.monotonic() + timeout_s
    last_window: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        body, rec = await client.get_current_window(
            book_id, chapter_idx, paragraph_idx=paragraph_idx
        )
        validate_window_response(body, rec)
        window = body.get("window")
        if window:
            last_window = window
            status = window.get("status")
            if status == "done":
                session.ingest_events(trace)
                return window
            if status == "failed":
                if await _handle_failed_window(window):
                    deadline = time.monotonic() + timeout_s
                continue

            target_window_id = window.get("id")
            remaining = deadline - time.monotonic()
            if target_window_id is not None and remaining > 0:
                evt = await session.collector.wait_for_event(
                    ("window.done", "window.failed"),
                    timeout_s=min(remaining, 2.0),
                    predicate=lambda e, wid=target_window_id: (
                        _chapter_filter(e)
                        and int(e.window_id or e.data.get("window_id") or -1) == wid
                    ),
                )
                if evt:
                    session.ingest_events(trace)
                    if evt.event_type == "window.failed":
                        failed_window = evt.data if isinstance(evt.data, dict) else {}
                        if not failed_window.get("id"):
                            failed_window = {
                                **failed_window,
                                "id": evt.window_id or target_window_id,
                            }
                        if await _handle_failed_window(failed_window):
                            deadline = time.monotonic() + timeout_s
                        continue
                    body, rec = await client.get_current_window(
                        book_id, chapter_idx, paragraph_idx=paragraph_idx
                    )
                    validate_window_response(body, rec)
                    window = body.get("window")
                    if window and window.get("status") == "done":
                        return window
                    if window and window.get("status") == "failed":
                        if await _handle_failed_window(window):
                            deadline = time.monotonic() + timeout_s
                        continue
                    last_window = window or last_window
                    continue

        await asyncio.sleep(0.5)

    return last_window

async def wait_for_comments(
    client: TargetClient,
    book_id: int,
    chapter_idx: int,
    *,
    min_count: int = 1,
    timeout_s: float = 60.0,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body, _ = await client.list_comments(book_id, chapter_idx)
        items = body.get("items") or []
        if len(items) >= min_count:
            return items
        await asyncio.sleep(2.0)
    body, _ = await client.list_comments(book_id, chapter_idx)
    return body.get("items") or []

async def verify_comments_not_regenerated(
    client: TargetClient,
    book_id: int,
    chapter_idx: int,
    comments_before: dict[int, int],
    new_comment_events: list[SSEEvent],
) -> None:
    """Fetch comments and verify jump-back did not regenerate existing coverage."""
    if not comments_before:
        return

    assert_no_comment_recreated_events(
        new_comment_events, comments_before, chapter_idx
    )

    body, rec = await client.list_comments(book_id, chapter_idx)
    validate_comments_response(body, rec)
    assert_comment_ids_stable(body.get("items") or [], comments_before)


async def assert_comments_not_regenerated(
    client: TargetClient,
    book_id: int,
    chapter_idx: int,
    comments_before: dict[int, int],
    new_comment_events: list[SSEEvent],
) -> None:
    """Backward-compatible alias for :func:`verify_comments_not_regenerated`."""
    await verify_comments_not_regenerated(
        client,
        book_id,
        chapter_idx,
        comments_before,
        new_comment_events,
    )


def save_jump_failure_context(
    ctx: ScenarioContext | dict[str, Any],
    *,
    book_id: int,
    chapter_idx: int,
    expected_paragraph: int,
    window: dict[str, Any] | None,
    jobs: list[dict[str, Any]],
) -> None:
    payload = {
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "expected_paragraph_idx": expected_paragraph,
        "current_window": window,
        "jobs": jobs,
    }
    if isinstance(ctx, ScenarioContext):
        ctx.extras["jump_failure_context"] = payload
    else:
        ctx["jump_failure_context"] = payload


async def _advance_until_cross_chapter(
    client: TargetClient,
    ctx: ScenarioContext,
    *,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    session: ReadingSession,
    metrics: MetricsAggregator,
    config: VerifyConfig,
    scenario_id: str,
    step_id: str,
) -> ReadingSession:
    assert ctx.book_id is not None
    session = await advance_start_chapter_sync_then_cross(
        client,
        ctx,
        ctx.book_id,
        cursor,
        chapters,
        trace,
        session,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
        config=config,
    )
    ctx.reading_session = session
    ctx.final_paragraph_idx = cursor.paragraph_idx
    ctx.chapter_idx = cursor.chapter_idx
    return session


async def _advance_in_chapter_batch(
    client: TargetClient,
    ctx: ScenarioContext,
    *,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    metrics: MetricsAggregator,
    config: VerifyConfig,
    next_paragraph: int,
    batch_size: int,
    scenario_id: str,
    step_id: str,
) -> int:
    """Advance up to *batch_size* paragraphs within the cursor's current chapter."""
    chapter = chapter_by_idx(chapters, cursor.chapter_idx)
    if chapter is None:
        raise StepAssertionError(
            assertion="chapter_exists",
            message=f"Chapter {cursor.chapter_idx} not found in book metadata",
            actual={"chapter_idx": cursor.chapter_idx},
        )

    chapter_last = last_paragraph_idx(chapter)
    if next_paragraph > chapter_last:
        raise StepAssertionError(
            assertion="same_chapter_reading",
            message="Reached chapter end before min_comment_windows completed",
            actual={
                "chapter_idx": cursor.chapter_idx,
                "paragraph_idx": cursor.paragraph_idx,
                "chapter_last_paragraph_idx": chapter_last,
            },
        )

    end = min(next_paragraph + batch_size, chapter_last)
    assert ctx.book_id is not None
    last = await advance_reading(
        client,
        ctx,
        ctx.book_id,
        cursor.chapter_idx,
        next_paragraph,
        end,
        trace,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
        delay_ms=config.params.progress_step_delay_ms,
    )
    cursor.paragraph_idx = last
    ctx.final_paragraph_idx = last
    ctx.chapter_idx = cursor.chapter_idx
    return last + 1


async def _advance_until_comment_windows(
    client: TargetClient,
    ctx: ScenarioContext,
    *,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    session: ReadingSession,
    metrics: MetricsAggregator,
    config: VerifyConfig,
    min_windows: int,
    scenario_id: str,
    step_id: str,
) -> list[dict[str, Any]]:
    initial_batch = max(12, min_windows * 12)
    followup_batch = 12
    completed_windows: list[dict[str, Any]] = []
    next_paragraph = cursor.paragraph_idx
    assert ctx.book_id is not None
    book_id = ctx.book_id

    next_paragraph = await _advance_in_chapter_batch(
        client,
        ctx,
        cursor=cursor,
        chapters=chapters,
        trace=trace,
        metrics=metrics,
        config=config,
        next_paragraph=next_paragraph,
        batch_size=initial_batch,
        scenario_id=scenario_id,
        step_id=step_id,
    )

    while len(completed_windows) < min_windows:
        window = await wait_for_window_done(
            client,
            session,
            book_id,
            cursor.chapter_idx,
            cursor.paragraph_idx,
            float(config.params.max_wait_comment_window_s),
            trace,
        )
        if window and window.get("status") == "done":
            completed_windows.append(window)

        if len(completed_windows) >= min_windows:
            break

        next_paragraph = await _advance_in_chapter_batch(
            client,
            ctx,
            cursor=cursor,
            chapters=chapters,
            trace=trace,
            metrics=metrics,
            config=config,
            next_paragraph=next_paragraph,
            batch_size=followup_batch,
            scenario_id=scenario_id,
            step_id=step_id,
        )

    return completed_windows


async def _collect_chapter_comments_and_jobs(
    client: TargetClient,
    ctx: ScenarioContext,
    *,
    cursor: ReadingCursor,
    scenario_id: str,
    step_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_comments: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    assert ctx.book_id is not None
    book_id = ctx.book_id

    for chapter_idx in cursor.visited_chapters:
        comments = await wait_for_comments(
            client,
            book_id,
            chapter_idx,
            min_count=0,
            timeout_s=30.0,
        )
        body, rec = await client.list_comments(book_id, chapter_idx)
        validate_comments_response(body, rec)
        validate_no_span_in_comments(body, rec)
        all_comments.extend(comments)

        chapter_jobs = await fetch_verify_jobs(
            client,
            book_id,
            chapter_idx,
            scenario_id=scenario_id,
            step_id=step_id,
        )
        jobs.extend(chapter_jobs)

    return all_comments, jobs


async def advance_for_a2_comments(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "advance_for_comments",
) -> None:
    """Advance reading per A2 stop mode and collect comment window evidence."""
    config = ctx.config
    metrics = ctx.metrics
    trace = ctx.reading_trace
    session = ctx.reading_session
    assert session is not None
    cursor = ctx.cursor
    assert isinstance(cursor, ReadingCursor)
    chapters: list[dict[str, Any]] = ctx.chapters or []
    long_flow = config.params.long_flow
    stop_mode = long_flow.reading_stop_mode
    min_windows = long_flow.min_comment_windows

    completed_windows: list[dict[str, Any]] = []
    all_comments: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []

    async with TargetClient(
        config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        if stop_mode == READING_STOP_CROSS_CHAPTER:
            session = await _advance_until_cross_chapter(
                client,
                ctx,
                cursor=cursor,
                chapters=chapters,
                trace=trace,
                session=session,
                metrics=metrics,
                config=config,
                scenario_id=scenario_id,
                step_id=step_id,
            )
        elif stop_mode == READING_STOP_COMMENT_WINDOWS:
            completed_windows = await _advance_until_comment_windows(
                client,
                ctx,
                cursor=cursor,
                chapters=chapters,
                trace=trace,
                session=session,
                metrics=metrics,
                config=config,
                min_windows=min_windows,
                scenario_id=scenario_id,
                step_id=step_id,
            )
        else:
            raise StepAssertionError(
                assertion="reading_stop_mode",
                message="Unsupported params.long_flow.reading_stop_mode",
                actual={"reading_stop_mode": stop_mode},
            )

        all_comments, jobs = await _collect_chapter_comments_and_jobs(
            client,
            ctx,
            cursor=cursor,
            scenario_id=scenario_id,
            step_id=step_id,
        )
    if stop_mode == READING_STOP_CROSS_CHAPTER:
        assert_that.gte(
            cursor.chapters_crossed,
            1,
            label="cross_chapter_reading",
        )
    elif stop_mode == READING_STOP_COMMENT_WINDOWS:
        assert_that.gte(
            len(completed_windows),
            min_windows,
            label="real_comment_windows_completed",
        )

    for window in completed_windows:
        window_comments = [
            c
            for c in all_comments
            if window.get("id") is None or c.get("window_id") == window.get("id")
        ]
        assert_comments_valid(
            window_comments,
            window=window,
            allow_no_call=True,
            config=config,
        )

    ctx.completed_windows = completed_windows
    ctx.comments = all_comments
    ctx.verify_jobs = jobs
    ctx.extras["chapters_crossed"] = cursor.chapters_crossed
    ctx.extras["visited_chapters"] = list(cursor.visited_chapters)
    ctx.extras["reading_stop_mode"] = stop_mode
    ctx.run_manager.real_llm_tracker.phase_coverage["A2_comments"] = True

    record_comment_metrics(
        metrics,
        trace,
        scenario_id=scenario_id,
        step_id=step_id,
        jobs=jobs,
        comments=all_comments,
        window=completed_windows[-1] if completed_windows else None,
        config=config,
    )


async def wait_s2_window_done(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "wait_window_done",
) -> None:
    """Wait for the S2 comment window to complete and capture window state."""
    assert ctx.reading_session is not None
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    assert ctx.final_paragraph_idx is not None

    final_paragraph_idx = ctx.final_paragraph_idx
    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        window = await wait_for_window_done(
            client,
            ctx.reading_session,
            ctx.book_id,
            ctx.chapter_idx,
            final_paragraph_idx,
            float(ctx.config.params.max_wait_comment_window_s),
            ctx.reading_trace,
        )
        ctx.reading_session.ingest_events(ctx.reading_trace)

        body, rec = await client.get_current_window(
            ctx.book_id,
            ctx.chapter_idx,
            paragraph_idx=final_paragraph_idx,
        )
        validate_window_response(body, rec)
        completed_window = body.get("window")
        if window is not None and completed_window is None:
            completed_window = window
        ctx.completed_window = completed_window

async def verify_s2_comments(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "verify_comments",
) -> None:
    """Query comments API, validate contract, and record SSE comment metrics."""
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    session = ctx.reading_session
    window = ctx.completed_window

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        comments = await wait_for_comments(
            client,
            ctx.book_id,
            ctx.chapter_idx,
            min_count=0,
            timeout_s=float(ctx.config.params.max_wait_comment_window_s),
        )

        body, rec = await client.list_comments(ctx.book_id, ctx.chapter_idx)
        validate_comments_response(body, rec)
        validate_no_span_in_comments(body, rec)
        validation_failures = assert_comments_valid(
            comments,
            window=window,
            allow_no_call=True,
            config=ctx.config,
        )
        ctx.extras["window_no_call"] = window_is_no_call(window, comments)
        ctx.extras["validation_failures"] = validation_failures
        ctx.comments = comments
        if session:
            session.ingest_events(ctx.reading_trace)
            session.record_comment_event_metrics(
                ctx.reading_trace,
                ctx.metrics,
                scenario_id=scenario_id,
                step_id=step_id,
            )


async def verify_s2_window_dedup(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "verify_window_dedup",
) -> None:
    """Identical progress should not re-queue the same comment window."""
    assert ctx.reading_session is not None
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    assert ctx.final_paragraph_idx is not None

    queued_before = ctx.reading_trace.window_queued_count
    final = ctx.final_paragraph_idx

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        first, rec = await client.update_progress(
            ctx.book_id, ctx.chapter_idx, final, 0.35
        )
        validate_progress_response(first, rec)
        first_jobs = len(first.get("jobs") or [])
        await asyncio.sleep(1.1)
        second, rec = await client.update_progress(
            ctx.book_id, ctx.chapter_idx, final, 0.35
        )
        validate_progress_response(second, rec)
        second_jobs = len(second.get("jobs") or [])

        ctx.metrics.record_from_api_record(
            rec, scenario_id=scenario_id, step_id=step_id
        )

        if progress_update_was_deduped(first, second):
            ctx.reading_trace.progress_dedup_count += 1

        ctx.reading_session.ingest_events(ctx.reading_trace)
        queued_after = ctx.reading_trace.window_queued_count
        assert_that.is_true(
            queued_after == queued_before,
            "Identical progress should not enqueue additional windows",
        )
        if not progress_update_was_deduped(first, second) and (
            second_jobs > 0 or first_jobs > 0
        ):
            raise StepAssertionError(
                assertion="window_dedup",
                message=(
                    "Identical progress re-queued jobs without dedup markers; "
                    f"first_jobs={first_jobs}, second_jobs={second_jobs}"
                ),
                expected="deduped progress update",
                actual={"first": first, "second": second},
            )

async def verify_s3_comment_reuse(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "verify_comment_reuse",
) -> None:
    """Completed comments must be reused after jump-back."""
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    session = ctx.reading_session
    assert session is not None

    comments_before: dict[int, int] = ctx.comments_before_jump_back or {}
    event_count_before = ctx.comment_event_count_before_jump_back

    if not comments_before:
        ctx.extras["comment_reuse_skipped"] = True
        return

    session.ingest_events(ctx.reading_trace)
    new_events = ctx.reading_trace.comment_events[event_count_before:]

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        await verify_comments_not_regenerated(
            client,
            ctx.book_id,
            ctx.chapter_idx,
            comments_before,
            new_events,
        )

async def verify_s3_final_window(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "verify_final_window",
) -> None:
    """Final window must align with latest reading position after jump-back."""
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    assert ctx.final_paragraph_idx is not None
    session = ctx.reading_session
    assert session is not None
    expected = ctx.final_paragraph_idx

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        progress, _ = await client.get_progress(ctx.book_id)
        assert_that.equal(
            progress.get("paragraph_idx"),
            expected,
            label="saved_progress_paragraph_idx",
        )

        body, rec = await client.get_current_window(
            ctx.book_id,
            ctx.chapter_idx,
            paragraph_idx=expected,
        )
        validate_window_response(body, rec)
        window = body.get("window")
        ctx.completed_window = window

        ctx.metrics.record_from_api_record(
            rec, scenario_id=scenario_id, step_id=step_id
        )

        if window is None:
            save_jump_failure_context(
                ctx,
                book_id=ctx.book_id,
                chapter_idx=ctx.chapter_idx,
                expected_paragraph=expected,
                window=window,
                jobs=await fetch_verify_jobs(
                    client,
                    ctx.book_id,
                    ctx.chapter_idx,
                    scenario_id=scenario_id,
                    step_id=step_id,
                ),
            )
            raise StepAssertionError(
                assertion="window_exists",
                message="Expected a current window after jump reading",
                actual=body,
            )

        covers = window_covers_paragraph(window, expected)
        frontier = window.get("assistant_frontier_paragraph_idx")
        frontier_ok = frontier is None or frontier >= expected

        if not covers and not frontier_ok:
            jobs = await fetch_verify_jobs(
                client,
                ctx.book_id,
                ctx.chapter_idx,
                scenario_id=scenario_id,
                step_id=step_id,
            )
            save_jump_failure_context(
                ctx,
                book_id=ctx.book_id,
                chapter_idx=ctx.chapter_idx,
                expected_paragraph=expected,
                window=window,
                jobs=jobs,
            )
            raise StepAssertionError(
                assertion="window_aligns_with_reading",
                message=(
                    f"Window [{window.get('start_paragraph_idx')}, "
                    f"{window.get('end_paragraph_idx')}] does not cover paragraph "
                    f"{expected} (frontier={frontier})"
                ),
                expected=expected,
                actual=window,
            )

        session.ingest_events(ctx.reading_trace)

async def verify_s3_jobs_stable(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "verify_jobs_stable",
) -> None:
    """Running jobs must not overwrite the current window after jump-back."""
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    assert ctx.final_paragraph_idx is not None

    window = ctx.completed_window or {}
    current_window_id = window.get("id")
    trace = ctx.reading_trace

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        jobs = await fetch_verify_jobs(
            client,
            ctx.book_id,
            ctx.chapter_idx,
            scenario_id=scenario_id,
            step_id=step_id,
        )
        ctx.extras["verify_jobs_snapshot"] = jobs

        running = [j for j in jobs if j.get("status") == "running"]
        for job in running:
            job_window_id = job.get("window_id")
            if (
                current_window_id is not None
                and job_window_id is not None
                and int(job_window_id) != int(current_window_id)
            ):
                trace.stale_job_ignored_count += 1

        if current_window_id is not None:
            latest_body, rec = await client.get_current_window(
                ctx.book_id,
                ctx.chapter_idx,
                paragraph_idx=ctx.final_paragraph_idx,
            )
            validate_window_response(latest_body, rec)
            latest_window = latest_body.get("window") or {}
            assert_that.equal(
                latest_window.get("id"),
                current_window_id,
                label="current_window_id_stable_after_jobs_check",
            )

# Re-export pure assertions for backward-compatible import paths.
__all__ = [
    "advance_for_a2_comments",
    "assert_comments_not_regenerated",
    "assert_comments_valid",
    "collect_validation_failures",
    "drain_chapter_comment_jobs",
    "progress_update_was_deduped",
    "raise_window_failed",
    "verify_comments_not_regenerated",
    "verify_s2_comments",
    "verify_s2_window_dedup",
    "verify_s3_comment_reuse",
    "verify_s3_final_window",
    "verify_s3_jobs_stable",
    "wait_for_comments",
    "wait_for_window_done",
    "wait_s2_window_done",
    "window_covers_paragraph",
    "window_is_no_call",
]
