"""Reading session, trace, cursor, and progress advancement."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.client_factory import TargetClient
from ..core.config import VerifyConfig
from ..core.context import ScenarioContext
from ..assertions.api_contracts import validate_comments_response, validate_progress_response
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..assertions.runtime import assert_reading_not_blocked_timing
from ..core.scenario import StepAssertionError
from ..sse_collector import SSEEvent, SSEEventCollector

from .corpus import chapter_by_idx, last_paragraph_idx, load_chapter_paragraphs, next_chapter_idx


@dataclass
class ReadingTrace:
    """Collects progress/window/comment observations during a scenario."""

    progress_update_count: int = 0
    progress_dedup_count: int = 0
    window_queued_count: int = 0
    window_done_count: int = 0
    window_failed_count: int = 0
    comment_created_count: int = 0
    stale_job_ignored_count: int = 0
    window_resolution_count: int = 0
    progress_durations_ms: list[float] = field(default_factory=list)
    window_e2e_latencies_ms: list[float] = field(default_factory=list)
    window_queued_at: dict[int, float] = field(default_factory=dict)
    completed_windows: list[dict[str, Any]] = field(default_factory=list)
    failed_windows: list[dict[str, Any]] = field(default_factory=list)
    comment_events: list[SSEEvent] = field(default_factory=list)
    compaction_done_count: int = 0
    compaction_failed_count: int = 0
    compaction_events: list[SSEEvent] = field(default_factory=list)
    completed_compactions: list[dict[str, Any]] = field(default_factory=list)


class ReadingSession:
    """SSE collector lifecycle tied to a book/chapter."""

    def __init__(
        self,
        base_url: str,
        run_manager: RunManager,
        scenario_id: str,
        book_id: int,
        chapter_idx: int,
    ):
        self.collector = SSEEventCollector(
            base_url, run_manager, verify_scenario_id=scenario_id
        )
        self.book_id = book_id
        self.chapter_idx = chapter_idx
        self._started = False
        self._ingested_event_count = 0
        self._recorded_comment_event_count = 0

    async def start(self) -> None:
        await self.collector.start(
            params={"book_id": self.book_id, "chapter_idx": self.chapter_idx}
        )
        self._started = True

    async def stop(self) -> None:
        if self._started:
            await self.collector.stop()
            self._started = False

    async def __aenter__(self) -> ReadingSession:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    def ingest_events(self, trace: ReadingTrace) -> None:
        """Process only SSE events not yet ingested into *trace*."""
        events = self.collector.events
        for evt in events[self._ingested_event_count :]:
            if evt.book_id != self.book_id:
                continue
            if evt.chapter_idx is not None and evt.chapter_idx != self.chapter_idx:
                continue

            self._ingest_single_event(trace, evt)

        self._ingested_event_count = len(events)

    def _ingest_single_event(self, trace: ReadingTrace, evt: SSEEvent) -> None:
        if evt.event_type == "window.queued":
            trace.window_queued_count += 1
            window_id = evt.window_id or evt.data.get("window_id")
            if window_id is not None:
                trace.window_queued_at.setdefault(int(window_id), time.monotonic())
        elif evt.event_type == "window.done":
            trace.window_done_count += 1
            window_id = evt.window_id or evt.data.get("window_id")
            if window_id is not None:
                wid = int(window_id)
                started = trace.window_queued_at.get(wid)
                if started is not None:
                    trace.window_e2e_latencies_ms.append(
                        (time.monotonic() - started) * 1000
                    )
                trace.completed_windows.append(evt.data)
        elif evt.event_type == "window.failed":
            trace.window_failed_count += 1
            trace.failed_windows.append(evt.data)
        elif evt.event_type == "comment.created":
            trace.comment_created_count += 1
            trace.comment_events.append(evt)
        elif evt.event_type == "context.compacted":
            trace.compaction_done_count += 1
            trace.compaction_events.append(evt)
            trace.completed_compactions.append(evt.data)
        elif evt.event_type == "job.failed":
            job_type = evt.data.get("job_type")
            if job_type == "compact_context":
                trace.compaction_failed_count += 1

    def record_comment_event_metrics(
        self,
        trace: ReadingTrace,
        metrics: MetricsAggregator,
        *,
        scenario_id: str,
        step_id: str,
    ) -> None:
        """Write trace index entries only for comment events not yet recorded."""
        for evt in trace.comment_events[self._recorded_comment_event_count :]:
            metrics.record_sse_event_metrics(
                evt,
                scenario_id=scenario_id,
                step_id=step_id,
            )
        self._recorded_comment_event_count = len(trace.comment_events)


async def update_progress(
    client: TargetClient,
    ctx: ScenarioContext | dict[str, Any],
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    scroll_pct: float,
    trace: ReadingTrace,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
) -> dict[str, Any]:
    body, rec = await client.update_progress(
        book_id, chapter_idx, paragraph_idx, scroll_pct
    )
    validate_progress_response(body, rec)

    trace.progress_update_count += 1
    if rec.duration_ms is not None:
        trace.progress_durations_ms.append(rec.duration_ms)
        metrics.record(
            "progress.update.duration_ms",
            rec.duration_ms,
            unit="ms",
            scenario_id=scenario_id,
            step_id=step_id,
            tags={"paragraph_idx": paragraph_idx},
        )

    metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)

    jobs = body.get("jobs") or []
    if jobs:
        trace.window_resolution_count += 1

    if isinstance(ctx, ScenarioContext):
        ctx.last_progress_response = body
    else:
        ctx["last_progress_response"] = body
    return body


async def advance_reading_to(
    client: TargetClient,
    ctx: ScenarioContext | dict[str, Any],
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    trace: ReadingTrace,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
) -> int:
    """Jump reading progress to *paragraph_idx* in one PUT."""
    await update_progress(
        client,
        ctx,
        book_id,
        chapter_idx,
        paragraph_idx,
        0.35,
        trace,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
    )
    return paragraph_idx


async def advance_reading(
    client: TargetClient,
    ctx: ScenarioContext | dict[str, Any],
    book_id: int,
    chapter_idx: int,
    start_paragraph: int,
    end_paragraph: int,
    trace: ReadingTrace,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
    delay_ms: int = 300,
) -> int:
    if end_paragraph < start_paragraph:
        start_paragraph, end_paragraph = end_paragraph, start_paragraph

    last = start_paragraph
    for paragraph_idx in range(start_paragraph, end_paragraph + 1):
        await update_progress(
            client,
            ctx,
            book_id,
            chapter_idx,
            paragraph_idx,
            0.35,
            trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=metrics,
        )
        last = paragraph_idx
        if delay_ms > 0 and paragraph_idx < end_paragraph:
            await asyncio.sleep(delay_ms / 1000.0)
    return last


@dataclass
class ReadingCursor:
    """Tracks reading position while advancing across chapter boundaries."""

    chapter_idx: int
    paragraph_idx: int
    chapters_crossed: int = 0
    visited_chapters: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.chapter_idx not in self.visited_chapters:
            self.visited_chapters.append(self.chapter_idx)

async def switch_reading_session(
    ctx: ScenarioContext,
    session: ReadingSession | None,
    *,
    scenario_id: str,
    book_id: int,
    chapter_idx: int,
) -> ReadingSession:
    if session is not None:
        await session.stop()
    new_session = ReadingSession(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        book_id,
        chapter_idx,
    )
    await new_session.start()
    return new_session


def _move_cursor_to_next_chapter(
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
) -> bool:
    next_idx = next_chapter_idx(chapters, cursor.chapter_idx)
    if next_idx is None:
        return False
    previous_chapter = cursor.chapter_idx
    cursor.chapter_idx = next_idx
    cursor.paragraph_idx = 0
    if previous_chapter != next_idx:
        cursor.chapters_crossed += 1
    if next_idx not in cursor.visited_chapters:
        cursor.visited_chapters.append(next_idx)
    return True


async def _cross_reading_chapter(
    ctx: ScenarioContext,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    session: ReadingSession,
    *,
    scenario_id: str,
    book_id: int,
) -> tuple[ReadingSession, bool]:
    if not _move_cursor_to_next_chapter(cursor, chapters):
        return session, False
    new_session = await switch_reading_session(
        ctx,
        session,
        scenario_id=scenario_id,
        book_id=book_id,
        chapter_idx=cursor.chapter_idx,
    )
    return new_session, True


async def advance_reading_cross_chapter(
    client: TargetClient,
    ctx: ScenarioContext,
    book_id: int,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    paragraph_steps: int,
    trace: ReadingTrace,
    session: ReadingSession,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
    delay_ms: int = 300,
) -> ReadingSession:
    """Advance *paragraph_steps* forward, crossing chapter boundaries as needed."""
    if paragraph_steps <= 0:
        return session

    remaining = paragraph_steps
    active_session = session

    while remaining > 0:
        chapter = chapter_by_idx(chapters, cursor.chapter_idx)
        if chapter is None:
            raise StepAssertionError(
                assertion="chapter_exists",
                message=f"Chapter {cursor.chapter_idx} not found in book metadata",
                actual={"chapter_idx": cursor.chapter_idx},
            )

        chapter_last = last_paragraph_idx(chapter)
        if (
            chapter.get("paragraph_count", 0) <= 0
            or cursor.paragraph_idx > chapter_last
        ):
            active_session, moved = await _cross_reading_chapter(
                ctx,
                cursor,
                chapters,
                active_session,
                scenario_id=scenario_id,
                book_id=book_id,
            )
            if not moved:
                break
            continue

        step_end = min(cursor.paragraph_idx + remaining, chapter_last)
        if step_end == cursor.paragraph_idx:
            active_session, moved = await _cross_reading_chapter(
                ctx,
                cursor,
                chapters,
                active_session,
                scenario_id=scenario_id,
                book_id=book_id,
            )
            if not moved:
                break
            continue

        await advance_reading(
            client,
            ctx,
            book_id,
            cursor.chapter_idx,
            cursor.paragraph_idx,
            step_end,
            trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=metrics,
            delay_ms=delay_ms,
        )
        consumed = step_end - cursor.paragraph_idx
        cursor.paragraph_idx = step_end
        remaining -= consumed

        if remaining <= 0 or cursor.paragraph_idx < chapter_last:
            break

        active_session, moved = await _cross_reading_chapter(
            ctx,
            cursor,
            chapters,
            active_session,
            scenario_id=scenario_id,
            book_id=book_id,
        )
        if not moved:
            break

    return active_session

async def advance_start_chapter_sync_then_cross(
    client: TargetClient,
    ctx: ScenarioContext,
    book_id: int,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    session: ReadingSession,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
    config: VerifyConfig,
    read_batch_size: int | None = None,
) -> ReadingSession:
    """Read the start chapter in paced batches, waiting for comment windows between batches."""
    from .comments import drain_chapter_comment_jobs, wait_for_window_done

    start_chapter_idx = cursor.chapter_idx
    chapter = chapter_by_idx(chapters, start_chapter_idx)
    if chapter is None:
        raise StepAssertionError(
            assertion="chapter_exists",
            message=f"Chapter {start_chapter_idx} not found in book metadata",
            actual={"chapter_idx": start_chapter_idx},
        )

    chapter_last = last_paragraph_idx(chapter)
    if read_batch_size is None:
        read_batch_size = config.params.read_batch_size
    delay_ms = config.params.progress_step_delay_ms
    max_wait = float(config.params.max_wait_comment_window_s)

    next_paragraph = cursor.paragraph_idx
    while cursor.chapter_idx == start_chapter_idx and next_paragraph <= chapter_last:
        batch_end = min(next_paragraph + read_batch_size - 1, chapter_last)
        last = await advance_reading(
            client,
            ctx,
            book_id,
            start_chapter_idx,
            next_paragraph,
            batch_end,
            trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=metrics,
            delay_ms=delay_ms,
        )
        cursor.paragraph_idx = last
        ctx.final_paragraph_idx = last
        ctx.chapter_idx = cursor.chapter_idx
        next_paragraph = last + 1

        await wait_for_window_done(
            client,
            session,
            book_id,
            start_chapter_idx,
            last,
            max_wait,
            trace,
            retry_on_failure=True,
        )

    await drain_chapter_comment_jobs(
        client,
        session,
        book_id,
        start_chapter_idx,
        chapter_last,
        trace,
        scenario_id=scenario_id,
        step_id=step_id,
        config=config,
    )

    if cursor.chapters_crossed >= 1:
        return session

    active_session = await advance_reading_cross_chapter(
        client,
        ctx,
        book_id,
        cursor,
        chapters,
        max(read_batch_size, 8),
        trace,
        session,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
        delay_ms=delay_ms,
    )
    if cursor.chapters_crossed < 1:
        raise StepAssertionError(
            assertion="cross_chapter_reading",
            message="Finished start chapter but did not enter the next chapter",
            actual={
                "chapter_idx": cursor.chapter_idx,
                "paragraph_idx": cursor.paragraph_idx,
                "start_chapter_idx": start_chapter_idx,
                "chapter_last_paragraph_idx": chapter_last,
            },
        )
    return active_session

async def advance_until_chapter_crossed(
    client: TargetClient,
    ctx: ScenarioContext,
    book_id: int,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    session: ReadingSession,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
    delay_ms: int = 300,
    batch_size: int = 24,
) -> ReadingSession:
    """Read forward paragraph-by-paragraph until the next chapter is entered."""
    active_session = session
    while cursor.chapters_crossed < 1:
        active_session = await advance_reading_cross_chapter(
            client,
            ctx,
            book_id,
            cursor,
            chapters,
            batch_size,
            trace,
            active_session,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=metrics,
            delay_ms=delay_ms,
        )
        if cursor.chapters_crossed >= 1:
            break
        if next_chapter_idx(chapters, cursor.chapter_idx) is None:
            raise StepAssertionError(
                assertion="cross_chapter_reading",
                message="Book ended before a second chapter could be reached",
                actual={
                    "chapter_idx": cursor.chapter_idx,
                    "paragraph_idx": cursor.paragraph_idx,
                },
            )
    return active_session

async def cross_to_next_chapter(
    client: TargetClient,
    ctx: ScenarioContext,
    book_id: int,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    session: ReadingSession,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
) -> ReadingSession:
    """Jump reading progress to the next chapter and restart SSE subscription."""
    next_idx = next_chapter_idx(chapters, cursor.chapter_idx)
    if next_idx is None:
        raise StepAssertionError(
            assertion="cross_chapter_reading",
            message="No next chapter available for cross-chapter reading",
            actual={
                "chapter_idx": cursor.chapter_idx,
                "paragraph_idx": cursor.paragraph_idx,
            },
        )

    await update_progress(
        client,
        ctx,
        book_id,
        next_idx,
        0,
        0.0,
        trace,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
    )

    previous_chapter = cursor.chapter_idx
    cursor.chapter_idx = next_idx
    cursor.paragraph_idx = 0
    if previous_chapter != next_idx:
        cursor.chapters_crossed += 1
    if next_idx not in cursor.visited_chapters:
        cursor.visited_chapters.append(next_idx)

    return await switch_reading_session(
        ctx,
        session,
        scenario_id=scenario_id,
        book_id=book_id,
        chapter_idx=next_idx,
    )

async def start_reading_sse(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "start_sse",
) -> None:
    """Subscribe to window SSE at the reading start chapter."""
    assert ctx.book_id is not None
    start_chapter_idx = ctx.start_chapter_idx if ctx.start_chapter_idx is not None else ctx.chapter_idx
    assert start_chapter_idx is not None
    session = ReadingSession(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        ctx.book_id,
        int(start_chapter_idx),
    )
    await session.start()
    ctx.reading_session = session


async def probe_reading_progress_latency(
    client: TargetClient,
    ctx: ScenarioContext | dict[str, Any],
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    trace: ReadingTrace,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
) -> float:
    """Update progress once and return elapsed milliseconds."""
    start = time.monotonic()
    await update_progress(
        client,
        ctx,
        book_id,
        chapter_idx,
        paragraph_idx,
        0.5,
        trace,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
    )
    return (time.monotonic() - start) * 1000


async def assert_reading_not_blocked(
    client: TargetClient,
    ctx: ScenarioContext | dict[str, Any],
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    trace: ReadingTrace,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
    max_duration_ms: float = 5000.0,
) -> None:
    """Backward-compatible wrapper: probe progress latency then assert budget."""
    elapsed_ms = await probe_reading_progress_latency(
        client,
        ctx,
        book_id,
        chapter_idx,
        paragraph_idx,
        trace,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
    )
    assert_reading_not_blocked_timing(elapsed_ms, max_duration_ms=max_duration_ms)


async def start_s2_reading_sse(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "start_sse",
) -> None:
    """Subscribe to window and comment SSE events for S2 continuous reading."""
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    session = ReadingSession(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        ctx.book_id,
        ctx.chapter_idx,
    )
    await session.start()
    ctx.reading_session = session


async def advance_s2_reading(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "advance_reading",
) -> None:
    """Advance reading from the early probe to trigger a comment window."""
    assert ctx.probe is not None
    assert ctx.chapter_paragraphs
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None

    start = ctx.probe.paragraph_idx
    last_idx = ctx.chapter_paragraphs[-1]["paragraph_idx"]
    end = min(start + 12, last_idx)

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        final = await advance_reading(
            client,
            ctx,
            ctx.book_id,
            ctx.chapter_idx,
            start,
            end,
            ctx.reading_trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=ctx.metrics,
            delay_ms=ctx.config.params.progress_step_delay_ms,
        )
        ctx.final_paragraph_idx = final


async def verify_s2_reading_not_blocked(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "verify_not_blocked",
) -> None:
    """Confirm progress updates remain fast while comments exist."""
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    assert ctx.final_paragraph_idx is not None

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        await assert_reading_not_blocked(
            client,
            ctx,
            ctx.book_id,
            ctx.chapter_idx,
            ctx.final_paragraph_idx,
            ctx.reading_trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=ctx.metrics,
        )


async def _ensure_chapter_paragraphs(
    ctx: ScenarioContext,
    chapter_idx: int,
) -> None:
    """Load chapter paragraphs when reading jumps to a different chapter."""
    if ctx.chapter_idx == chapter_idx and ctx.chapter_paragraphs:
        return
    assert ctx.book_id is not None
    ctx.chapter_idx = chapter_idx
    ctx.chapter_paragraphs = await load_chapter_paragraphs(ctx, ctx.book_id, chapter_idx)


async def start_s3_reading_sse(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "start_sse",
) -> None:
    """Subscribe to window SSE events for S3 fast scroll."""
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    session = ReadingSession(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        ctx.book_id,
        ctx.chapter_idx,
    )
    await session.start()
    ctx.reading_session = session


async def start_s4_reading_sse(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "start_sse",
) -> None:
    """Subscribe to window and compaction SSE events for S4 long context."""
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None
    session = ReadingSession(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        ctx.book_id,
        ctx.chapter_idx,
    )
    await session.start()
    ctx.reading_session = session


async def fast_scroll_s3(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "fast_scroll",
) -> None:
    """Rapidly report many paragraph positions within chapter 1."""
    assert ctx.probe is not None
    assert ctx.chapter_paragraphs
    assert ctx.book_id is not None
    assert ctx.chapter_idx is not None

    early = ctx.probe
    end = min(early.paragraph_idx + 25, ctx.chapter_paragraphs[-1]["paragraph_idx"])
    start = max(0, early.paragraph_idx - 5)

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        final = await advance_reading(
            client,
            ctx,
            ctx.book_id,
            ctx.chapter_idx,
            start,
            end,
            ctx.reading_trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=ctx.metrics,
            delay_ms=0,
        )
        ctx.extras["fast_scroll_end"] = final


async def jump_forward_s3(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "jump_forward",
) -> None:
    """Jump forward to the middle probe and snapshot comments before jump-back."""
    assert ctx.book_id is not None
    middle = ctx.extras.get("middle_probe")
    assert middle is not None

    await _ensure_chapter_paragraphs(ctx, middle.chapter_idx)
    target = min(middle.paragraph_idx, ctx.chapter_paragraphs[-1]["paragraph_idx"])

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        await update_progress(
            client,
            ctx,
            ctx.book_id,
            ctx.chapter_idx,
            target,
            0.2,
            ctx.reading_trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=ctx.metrics,
        )
        ctx.extras["jump_forward_paragraph"] = target

        body, rec = await client.list_comments(ctx.book_id, ctx.chapter_idx)
        validate_comments_response(body, rec)
        items = body.get("items") or []
        ctx.comments_before_jump_back = {
            item["paragraph_idx"]: item["id"]
            for item in items
            if item.get("paragraph_idx") is not None and item.get("id") is not None
        }
        session = ctx.reading_session
        assert session is not None
        session.ingest_events(ctx.reading_trace)
        ctx.comment_event_count_before_jump_back = len(
            ctx.reading_trace.comment_events
        )


async def jump_back_s3(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "jump_back",
) -> None:
    """Jump back to a nearby earlier paragraph after forward jump."""
    assert ctx.probe is not None
    assert ctx.book_id is not None
    early = ctx.probe

    await _ensure_chapter_paragraphs(ctx, early.chapter_idx)
    back_target = min(
        early.paragraph_idx + 5,
        ctx.chapter_paragraphs[-1]["paragraph_idx"],
    )

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        await update_progress(
            client,
            ctx,
            ctx.book_id,
            ctx.chapter_idx,
            back_target,
            0.6,
            ctx.reading_trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=ctx.metrics,
        )
        ctx.final_paragraph_idx = back_target
