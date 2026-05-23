"""Shared helpers for reading-progress and comment verification scenarios."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..client import TargetClient
from ..config import VerifyConfig
from ..contract import (
    validate_comments_response,
    validate_import_response,
    validate_progress_response,
    validate_window_response,
)
from ..corpus import BookManifest, CorpusManager, ProbeConfig
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import StepAssertionError, assert_that
from ..sse_collector import SSEEvent, SSEEventCollector

logger = logging.getLogger(__name__)


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


def merge_suite_ctx(ctx: dict[str, Any], suite_ctx: dict[str, Any] | None) -> None:
    if not suite_ctx:
        return
    for key in (
        "imported_book",
        "book_manifest",
        "chapters",
        "first_chapter_paragraphs",
        "import_stats",
    ):
        if key in suite_ctx and key not in ctx:
            ctx[key] = suite_ctx[key]


def publish_suite_ctx(ctx: dict[str, Any], suite_ctx: dict[str, Any] | None) -> None:
    if not suite_ctx:
        return
    for key in (
        "imported_book",
        "book_manifest",
        "chapters",
        "first_chapter_paragraphs",
        "import_stats",
        "comment_audit_exporter",
        "reading_trace",
    ):
        if key in ctx:
            suite_ctx[key] = ctx[key]


def get_probe(corpus: CorpusManager, name: str = "early") -> ProbeConfig:
    if not corpus.books:
        corpus.load()
    book = corpus.books[0]
    for probe in book.probes:
        if probe.name == name:
            return probe
    if book.probes:
        return book.probes[0]
    return ProbeConfig(name="default", chapter_idx=0, paragraph_idx=20)


async def ensure_imported_book(ctx: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    imported = ctx.get("imported_book")
    if imported and imported.get("id"):
        return imported["id"], imported

    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    corpus: CorpusManager = ctx["corpus"]

    if not corpus.books:
        corpus.load()
        corpus.validate()

    book_manifest: BookManifest = ctx.get("book_manifest") or corpus.books[0]
    ctx["book_manifest"] = book_manifest

    async with TargetClient(
        config.target.base_url,
        run_manager,
        ctx.get("scenario_id", "setup"),
        "ensure_import",
        context=ctx,
    ) as client:
        body, rec = await client.import_book(book_manifest.path)
        validate_import_response(body, rec)
        book = body["book"]
        ctx["imported_book"] = book
        ctx["import_stats"] = body.get("import_stats", {})
        return book["id"], book


async def load_chapter_paragraphs(
    ctx: dict[str, Any],
    book_id: int,
    chapter_idx: int,
) -> list[dict[str, Any]]:
    cache_key = f"paragraphs_{book_id}_{chapter_idx}"
    if cache_key in ctx:
        return ctx[cache_key]

    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        ctx.get("scenario_id", "setup"),
        "load_paragraphs",
        context=ctx,
    ) as client:
        body, _ = await client.list_paragraphs(book_id, chapter_idx, params={"limit": 5000})
        paragraphs = body.get("items", [])
        ctx[cache_key] = paragraphs
        return paragraphs


def paragraph_text_map(paragraphs: list[dict[str, Any]]) -> dict[int, str]:
    return {p["paragraph_idx"]: p.get("text", "") for p in paragraphs}


def neighbor_paragraphs(
    paragraphs: list[dict[str, Any]],
    paragraph_idx: int,
    radius: int = 1,
) -> list[dict[str, Any]]:
    by_idx = paragraph_text_map(paragraphs)
    neighbors: list[dict[str, Any]] = []
    for offset in (-radius, radius):
        idx = paragraph_idx + offset
        if idx in by_idx and idx != paragraph_idx:
            neighbors.append({"paragraph_idx": idx, "text": by_idx[idx]})
    return sorted(neighbors, key=lambda item: item["paragraph_idx"])


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

        self._ingested_event_count = len(events)

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
    ctx: dict[str, Any],
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

    ctx["last_progress_response"] = body
    return body


async def advance_reading(
    client: TargetClient,
    ctx: dict[str, Any],
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


def resolve_happy_path_start(probe: ProbeConfig, fallback: ProbeConfig) -> tuple[int, int]:
    """Return the start chapter/paragraph for real happy-path reading."""
    chapter_idx = (
        probe.start_chapter_idx
        if probe.start_chapter_idx is not None
        else fallback.chapter_idx
    )
    paragraph_idx = (
        probe.start_paragraph_idx
        if probe.start_paragraph_idx is not None
        else fallback.paragraph_idx
    )
    return chapter_idx, paragraph_idx


async def load_chapters(
    ctx: dict[str, Any],
    book_id: int,
    *,
    client: TargetClient | None = None,
) -> list[dict[str, Any]]:
    if ctx.get("chapters"):
        return ctx["chapters"]

    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    if client is None:
        async with TargetClient(
            config.target.base_url,
            run_manager,
            ctx.get("scenario_id", "setup"),
            "load_chapters",
            context=ctx,
        ) as owned_client:
            body, _ = await owned_client.list_chapters(book_id)
            chapters = body.get("items") or []
    else:
        body, _ = await client.list_chapters(book_id)
        chapters = body.get("items") or []

    ctx["chapters"] = chapters
    return chapters


def chapter_by_idx(chapters: list[dict[str, Any]], chapter_idx: int) -> dict[str, Any] | None:
    for chapter in chapters:
        if chapter.get("idx") == chapter_idx:
            return chapter
    return None


def last_paragraph_idx(chapter: dict[str, Any]) -> int:
    count = int(chapter.get("paragraph_count") or 0)
    return max(0, count - 1)


def next_chapter_idx(chapters: list[dict[str, Any]], current_idx: int) -> int | None:
    ordered = sorted(int(ch["idx"]) for ch in chapters)
    try:
        pos = ordered.index(current_idx)
    except ValueError:
        return None
    if pos + 1 >= len(ordered):
        return None
    return ordered[pos + 1]


async def switch_reading_session(
    ctx: dict[str, Any],
    session: ReadingSession | None,
    *,
    scenario_id: str,
    book_id: int,
    chapter_idx: int,
) -> ReadingSession:
    config: VerifyConfig = ctx["config"]
    if session is not None:
        await session.stop()
    new_session = ReadingSession(
        config.target.base_url,
        ctx["run_manager"],
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
    ctx: dict[str, Any],
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
    ctx: dict[str, Any],
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
        if chapter.get("paragraph_count", 0) <= 0 or cursor.paragraph_idx > chapter_last:
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


async def advance_until_chapter_crossed(
    client: TargetClient,
    ctx: dict[str, Any],
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
    """Keep reading forward until at least one chapter boundary is crossed."""
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
    ctx: dict[str, Any],
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


async def read_from_start_then_cross_chapter(
    client: TargetClient,
    ctx: dict[str, Any],
    book_id: int,
    cursor: ReadingCursor,
    chapters: list[dict[str, Any]],
    trace: ReadingTrace,
    session: ReadingSession,
    *,
    scenario_id: str,
    step_id: str,
    metrics: MetricsAggregator,
    warmup_paragraphs: int = 24,
    delay_ms: int = 300,
) -> ReadingSession:
    """Read from the start probe, then explicitly switch to the next chapter."""
    active_session = await advance_reading_cross_chapter(
        client,
        ctx,
        book_id,
        cursor,
        chapters,
        warmup_paragraphs,
        trace,
        session,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
        delay_ms=delay_ms,
    )
    if cursor.chapters_crossed >= 1:
        return active_session
    return await cross_to_next_chapter(
        client,
        ctx,
        book_id,
        cursor,
        chapters,
        trace,
        active_session,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
    )


async def wait_for_window_done(
    client: TargetClient,
    session: ReadingSession,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    timeout_s: float,
    trace: ReadingTrace,
) -> dict[str, Any] | None:
    """Wait for the window covering paragraph_idx to reach a terminal state."""

    def _chapter_filter(evt: SSEEvent) -> bool:
        return evt.book_id == book_id and evt.chapter_idx == chapter_idx

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
                raise_window_failed(window)

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
                        raise_window_failed(evt.data)
                    body, rec = await client.get_current_window(
                        book_id, chapter_idx, paragraph_idx=paragraph_idx
                    )
                    validate_window_response(body, rec)
                    window = body.get("window")
                    if window and window.get("status") == "done":
                        return window
                    if window and window.get("status") == "failed":
                        raise_window_failed(window)
                    last_window = window or last_window
                    continue

        await asyncio.sleep(0.5)

    return last_window


def raise_window_failed(window: dict[str, Any]) -> None:
    error = window.get("error") or window.get("failure") or {}
    message = error.get("message") if isinstance(error, dict) else str(error)
    code = error.get("code") if isinstance(error, dict) else "window_failed"
    raise StepAssertionError(
        assertion="window_failed",
        message=f"Comment window failed: {code} — {message or 'no details'}",
        expected="window.done or no-call window.done",
        actual=window,
    )


def window_is_no_call(window: dict[str, Any] | None, comments: list[dict[str, Any]]) -> bool:
    if not window:
        return False
    if window.get("no_call") is True:
        return True
    if window.get("status") == "done" and not comments:
        ready = window.get("comments_ready_count")
        target = window.get("comments_target_count")
        if ready == 0 and target is not None:
            return True
    return False


async def verify_backend_runtime(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "verify_runtime",
    require_verify_endpoint: bool = False,
    require_model_match: bool = False,
) -> None:
    """Call /verify/runtime and validate verify mode plus LLM configuration."""
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.verify_runtime()
        if rec.status_code == 404:
            if require_verify_endpoint:
                raise StepAssertionError(
                    assertion="verify_runtime_available",
                    message="Verify runtime endpoint required but returned 404",
                    actual={"status_code": rec.status_code},
                )
            ctx["verify_mode_active"] = False
            return

        assert_that.is_true(
            body.get("verify_mode", False),
            "Verify mode should be enabled",
        )

        llm = body.get("llm", {})
        if llm:
            assert_that.not_contains(
                str(llm), "sk-", "Verify runtime must not expose api_key"
            )

        backend_mode = llm.get("mode")
        if backend_mode is not None:
            assert_that.equal(
                backend_mode,
                config.llm.mode,
                label="backend_llm_mode_matches_verify_config",
            )

        if require_model_match:
            expected_model = config.effective_model()
            backend_model = llm.get("model")
            assert_that.is_not_none(backend_model, "verify runtime should expose llm.model")
            if expected_model:
                assert_that.equal(
                    backend_model,
                    expected_model,
                    label="backend_llm_model_matches_verify_config",
                )

        config_hash = body.get("config_hash") or llm.get("config_hash")
        if config_hash is not None:
            metrics.record(
                "verify.runtime.config_hash_available",
                1,
                unit="count",
                scenario_id=scenario_id,
                step_id=step_id,
            )
            ctx["backend_config_hash"] = config_hash
        else:
            metrics.record(
                "verify.runtime.config_hash_available",
                0,
                unit="count",
                scenario_id=scenario_id,
                step_id=step_id,
            )

        ctx["verify_mode_active"] = True
        ctx["backend_version"] = body.get("app_version")
        ctx["verify_runtime"] = body
        run_manager.set_backend_version(body.get("app_version"))

        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


def collect_validation_failures(
    comments: list[dict[str, Any]],
    window: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect desensitized validation failure summaries for audit export."""
    failures: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()

    for comment in comments:
        if not comment.get("validation_failed"):
            continue
        key = (comment.get("paragraph_idx"), comment.get("trace_id"))
        if key in seen:
            continue
        seen.add(key)
        failures.append(
            {
                "paragraph_idx": comment.get("paragraph_idx"),
                "trace_id": comment.get("trace_id"),
                "reason": comment.get("validation_error")
                or comment.get("discard_reason")
                or "validation_failed",
                "summary": _validation_failure_summary(comment),
            }
        )

    telemetry = window.get("comment_telemetry") if window else None
    if isinstance(telemetry, dict):
        for item in telemetry.get("validation_failures") or []:
            if not isinstance(item, dict):
                continue
            key = (item.get("paragraph_idx"), item.get("trace_id"))
            if key in seen:
                continue
            seen.add(key)
            failures.append(item)

    return failures


def _validation_failure_summary(comment: dict[str, Any]) -> str:
    text = str(comment.get("comment") or comment.get("validation_error") or "")
    text = text.strip()
    if len(text) <= 120:
        return text
    return text[:120] + "…"


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


def progress_update_was_deduped(first_body: dict[str, Any], second_body: dict[str, Any]) -> bool:
    """Return True when an identical progress PUT was treated as a no-op.

    Relies on explicit backend markers or stable ``updated_at``. Does not infer
    dedup from empty ``jobs`` alone — that would false-positive when a position
    simply does not trigger window resolution.
    """
    if second_body.get("deduped") is True or second_body.get("dedup") is True:
        return True

    first_progress = first_body.get("progress") or {}
    second_progress = second_body.get("progress") or {}
    first_updated = first_progress.get("updated_at")
    second_updated = second_progress.get("updated_at")
    return bool(first_updated and second_updated and first_updated == second_updated)


def _iso_duration_ms(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    from datetime import datetime

    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds() * 1000)


def record_comment_metrics(  # noqa: C901
    metrics: MetricsAggregator,
    trace: ReadingTrace,
    *,
    scenario_id: str,
    step_id: str,
    jobs: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
    window: dict[str, Any] | None = None,
    config: VerifyConfig | None = None,
) -> None:
    for latency in trace.window_e2e_latencies_ms:
        metrics.record(
            "comment.e2e_latency_ms",
            latency,
            unit="ms",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    total_progress = trace.progress_update_count
    dedup_hits = trace.progress_dedup_count
    if total_progress > 0:
        metrics.record(
            "window_dedup_hit_rate",
            dedup_hits / total_progress,
            unit="ratio",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    if trace.comment_created_count > 0:
        metrics.record(
            "comment.created.count",
            trace.comment_created_count,
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    if trace.window_failed_count:
        metrics.record(
            "comment.window_failed_count",
            trace.window_failed_count,
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    metrics.record(
        "progress.update.count",
        trace.progress_update_count,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )
    metrics.record(
        "window_resolution_count",
        trace.window_resolution_count,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
    )
    if trace.stale_job_ignored_count:
        metrics.record(
            "stale_job_ignored_count",
            trace.stale_job_ignored_count,
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
        )

    comment_jobs = [
        job
        for job in (jobs or [])
        if job.get("job_type") in (None, "comment_window")
    ]
    for job in comment_jobs:
        queue_wait = _iso_duration_ms(job.get("created_at"), job.get("started_at"))
        if queue_wait is not None:
            metrics.record(
                "comment.job_queue_wait_ms",
                queue_wait,
                unit="ms",
                scenario_id=scenario_id,
                step_id=step_id,
                tags={"job_id": job.get("id")},
            )
        run_ms = _iso_duration_ms(job.get("started_at"), job.get("completed_at"))
        if run_ms is not None:
            metrics.record(
                "comment.job_run_ms",
                run_ms,
                unit="ms",
                scenario_id=scenario_id,
                step_id=step_id,
                tags={"job_id": job.get("id")},
            )

    window_metrics = _extract_window_comment_metrics(window, comments or [], config)
    for metric_name, value in window_metrics.items():
        if value is None:
            continue
        unit = "ratio" if metric_name.startswith("comment.density") else "count"
        if metric_name.startswith("tokens_per_comment"):
            unit = "tokens"
        metrics.record(
            metric_name,
            float(value),
            unit=unit,
            scenario_id=scenario_id,
            step_id=step_id,
        )

    if comments:
        token_totals = [
            (comment.get("tokens_in") or 0) + (comment.get("tokens_out") or 0)
            for comment in comments
            if comment.get("tokens_in") is not None or comment.get("tokens_out") is not None
        ]
        for total in token_totals:
            metrics.record(
                "tokens_per_comment",
                total,
                unit="tokens",
                scenario_id=scenario_id,
                step_id=step_id,
            )
        if token_totals:
            metrics.record(
                "tokens_per_comment_window",
                sum(token_totals),
                unit="tokens",
                scenario_id=scenario_id,
                step_id=step_id,
            )


def _parse_comment_telemetry(
    window: dict[str, Any] | None,
) -> tuple[int, int, dict[str, int], int | None, int | None, int]:
    validation_failed = 0
    discarded = 0
    discarded_by_reason: dict[str, int] = {}
    tool_call_count = window.get("tool_call_count") if window else None
    candidate_lookup_count = window.get("candidate_lookup_count") if window else None

    telemetry = window.get("comment_telemetry") if window else None
    if window and isinstance(telemetry, dict):
        validation_failed = int(telemetry.get("validation_failed_count") or 0)
        reasons = telemetry.get("discarded_by_reason") or {}
        if isinstance(reasons, dict):
            discarded_by_reason = {str(k): int(v) for k, v in reasons.items()}
        if tool_call_count is None:
            tool_call_count = telemetry.get("tool_call_count")
        if candidate_lookup_count is None:
            candidate_lookup_count = telemetry.get("candidate_lookup_count")
        return (
            validation_failed,
            int(telemetry.get("discarded_count") or 0),
            discarded_by_reason,
            tool_call_count,
            candidate_lookup_count,
            1,
        )

    return validation_failed, discarded, discarded_by_reason, tool_call_count, candidate_lookup_count, 0


def _extract_window_comment_metrics(
    window: dict[str, Any] | None,
    comments: list[dict[str, Any]],
    config: VerifyConfig | None,
) -> dict[str, float | int | None]:
    if not window and not comments:
        return {}

    valid_count = window.get("comments_ready_count") if window else None
    if valid_count is None:
        valid_count = len(comments)

    (
        validation_failed,
        discarded,
        discarded_by_reason,
        tool_call_count,
        candidate_lookup_count,
        telemetry_available,
    ) = _parse_comment_telemetry(window)

    density_actual = None
    stat_start = None
    stat_end = None
    soft_min = config.comment_density.soft_min if config else None
    stat_window = config.comment_density.stat_window_paragraphs if config else None

    if window:
        stat_start = window.get("density_stat_start_paragraph_idx")
        stat_end = window.get("density_stat_end_paragraph_idx")
        density_actual = window.get("comment_density_actual")
        if density_actual is None and stat_start is not None and stat_end is not None:
            span = max(1, int(stat_end) - int(stat_start) + 1)
            density_actual = float(valid_count or 0) / span
        elif density_actual is None and stat_window:
            stat_end = window.get("assistant_frontier_paragraph_idx") or window.get(
                "end_paragraph_idx"
            )
            if stat_end is not None:
                stat_start = max(0, int(stat_end) - stat_window + 1)
                span = max(1, stat_window)
                density_actual = float(valid_count or 0) / span

    metrics: dict[str, float | int | None] = {
        "comment.telemetry_available": telemetry_available if window else None,
        "comment.valid_count": valid_count,
        "comment.validation_failed_count": validation_failed,
        "comment.discarded_count": discarded,
        "comment.tool_call_count": tool_call_count,
        "comment.candidate_lookup_count": candidate_lookup_count,
        "comment.density.actual": density_actual,
        "comment.density.soft_min": soft_min,
        "comment.density.stat_start_paragraph_idx": stat_start,
        "comment.density.stat_end_paragraph_idx": stat_end,
    }
    for reason, count in discarded_by_reason.items():
        metrics[f"comment.discarded_by_reason.{reason}"] = count
    return metrics


async def assert_comments_not_regenerated(
    client: TargetClient,
    book_id: int,
    chapter_idx: int,
    comments_before: dict[int, int],
    new_comment_events: list[SSEEvent],
) -> None:
    """Assert jump-back did not regenerate comments for already-covered paragraphs."""
    if not comments_before:
        return

    for evt in new_comment_events:
        paragraph_idx = evt.paragraph_idx or evt.data.get("paragraph_idx")
        if paragraph_idx is None:
            continue
        if int(paragraph_idx) in comments_before:
            raise StepAssertionError(
                assertion="comment_reuse",
                message=(
                    f"comment.created emitted for already-completed paragraph "
                    f"{paragraph_idx} in chapter {chapter_idx}"
                ),
                expected="reuse existing comment",
                actual=evt.to_dict(),
            )

    body, rec = await client.list_comments(book_id, chapter_idx)
    validate_comments_response(body, rec)
    for paragraph_idx, comment_id in comments_before.items():
        current = next(
            (item for item in body.get("items") or [] if item.get("paragraph_idx") == paragraph_idx),
            None,
        )
        if current is None:
            continue
        assert_that.equal(
            current.get("id"),
            comment_id,
            label=f"comment_id_stable_for_paragraph_{paragraph_idx}",
        )


def assert_comments_valid(
    comments: list[dict[str, Any]],
    *,
    window: dict[str, Any] | None = None,
    allow_no_call: bool = True,
    config: VerifyConfig | None = None,
) -> list[dict[str, Any]]:
    validation_failures = collect_validation_failures(comments, window)

    window_id = window.get("id") if window else None
    scoped_comments = (
        [c for c in comments if c.get("window_id") == window_id]
        if window_id is not None
        else comments
    )

    if not scoped_comments:
        if validation_failures:
            raise StepAssertionError(
                assertion="comment_validation_failed",
                message=(
                    "Window completed with validation failures but no persisted comments"
                ),
                expected="valid comments or explicit no-call window",
                actual={"window": window, "validation_failures": validation_failures},
            )
        if allow_no_call and window_is_no_call(window, scoped_comments):
            return validation_failures
        if window and window.get("status") == "done":
            if config is not None and not config.is_real_llm:
                raise StepAssertionError(
                    assertion="done_with_zero_comments",
                    message=(
                        "Stub mode: done window with zero comments and no no_call marker"
                    ),
                    expected="persisted comments or explicit no-call window",
                    actual={"window": window, "comments": scoped_comments},
                )
            logger.warning(
                "Done window with zero comments and no no_call marker "
                "(scenario may need comment.telemetry or explicit no_call)"
            )
            return validation_failures
        raise StepAssertionError(
            assertion="comments_or_no_call",
            message="Expected persisted comments or a successful no-call window",
            actual={"window": window, "comments": scoped_comments},
        )

    focus_start = window.get("focus_start_paragraph_idx") if window else None
    focus_end = window.get("focus_end_paragraph_idx") if window else None

    for comment in scoped_comments:
        paragraph_idx = comment.get("paragraph_idx")
        assert_that.is_not_none(paragraph_idx, "comment.paragraph_idx")
        for forbidden in ("span_start", "span_end", "span"):
            assert_that.not_contains(
                comment,
                forbidden,
                label=f"comment[{paragraph_idx}] must not contain span fields",
            )
        if comment.get("validation_failed"):
            continue
        assert_that.is_true(
            bool(comment.get("comment", "").strip()) or comment.get("discarded"),
            f"comment[{paragraph_idx}] text must not be empty unless discarded",
        )
        if focus_start is not None and focus_end is not None:
            assert_that.is_true(
                focus_start <= paragraph_idx <= focus_end,
                f"comment paragraph {paragraph_idx} should be within focus range "
                f"[{focus_start}, {focus_end}]",
            )

    return validation_failures


def window_covers_paragraph(window: dict[str, Any] | None, paragraph_idx: int) -> bool:
    if not window:
        return False
    start = window.get("start_paragraph_idx")
    end = window.get("end_paragraph_idx")
    if start is None or end is None:
        return False
    return start <= paragraph_idx <= end


async def fetch_verify_jobs(
    client: TargetClient,
    book_id: int,
    chapter_idx: int,
    *,
    scenario_id: str = "",
    step_id: str = "",
) -> list[dict[str, Any]]:
    body, rec = await client.verify_jobs(
        params={"book_id": book_id, "chapter_idx": chapter_idx, "limit": 200}
    )
    if rec.status_code == 404:
        logger.warning(
            "%s/%s: GET /api/verify/jobs returned 404 — verify mode likely disabled; "
            "job latency metrics will be unavailable",
            scenario_id or "verify",
            step_id or "fetch_verify_jobs",
        )
        return []

    items = body.get("items") or []
    if not items:
        logger.warning(
            "%s/%s: GET /api/verify/jobs returned no items for book_id=%s chapter_idx=%s",
            scenario_id or "verify",
            step_id or "fetch_verify_jobs",
            book_id,
            chapter_idx,
        )
    return items


def save_jump_failure_context(
    ctx: dict[str, Any],
    *,
    book_id: int,
    chapter_idx: int,
    expected_paragraph: int,
    window: dict[str, Any] | None,
    jobs: list[dict[str, Any]],
) -> None:
    ctx["jump_failure_context"] = {
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "expected_paragraph_idx": expected_paragraph,
        "current_window": window,
        "jobs": jobs,
    }


async def assert_reading_not_blocked(
    client: TargetClient,
    ctx: dict[str, Any],
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
    elapsed_ms = (time.monotonic() - start) * 1000
    if elapsed_ms > max_duration_ms:
        raise StepAssertionError(
            assertion="reading_not_blocked",
            message=f"Progress update took {elapsed_ms:.0f}ms (> {max_duration_ms:.0f}ms)",
            actual=elapsed_ms,
            expected=max_duration_ms,
        )
