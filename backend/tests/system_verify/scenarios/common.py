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
    comment_created_count: int = 0
    stale_job_ignored_count: int = 0
    window_resolution_count: int = 0
    progress_durations_ms: list[float] = field(default_factory=list)
    window_e2e_latencies_ms: list[float] = field(default_factory=list)
    window_queued_at: dict[int, float] = field(default_factory=dict)
    completed_windows: list[dict[str, Any]] = field(default_factory=list)
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


async def wait_for_window_done(
    client: TargetClient,
    session: ReadingSession,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    timeout_s: float,
    trace: ReadingTrace,
) -> dict[str, Any] | None:
    evt = await session.collector.wait_for_event(
        "window.done",
        timeout_s=min(timeout_s, 30.0),
        predicate=lambda e: e.book_id == book_id and e.chapter_idx == chapter_idx,
    )
    if evt:
        session.ingest_events(trace)
        return evt.data

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
            if window.get("status") == "done":
                return window
        await asyncio.sleep(1.0)
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


def record_comment_metrics(
    metrics: MetricsAggregator,
    trace: ReadingTrace,
    *,
    scenario_id: str,
    step_id: str,
    jobs: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
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

    # TODO(spec S2): populate tokens_per_comment once verify trace summary or
    # comment telemetry exposes per-comment token totals from the backend.
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
) -> None:
    focus_start = window.get("focus_start_paragraph_idx") if window else None
    focus_end = window.get("focus_end_paragraph_idx") if window else None

    for comment in comments:
        paragraph_idx = comment.get("paragraph_idx")
        assert_that.is_not_none(paragraph_idx, "comment.paragraph_idx")
        for forbidden in ("span_start", "span_end", "span"):
            assert_that.not_contains(
                comment,
                forbidden,
                label=f"comment[{paragraph_idx}] must not contain span fields",
            )
        assert_that.is_true(
            bool(comment.get("comment", "").strip()),
            f"comment[{paragraph_idx}] text must not be empty",
        )
        if focus_start is not None and focus_end is not None:
            assert_that.is_true(
                focus_start <= paragraph_idx <= focus_end,
                f"comment paragraph {paragraph_idx} should be within focus range "
                f"[{focus_start}, {focus_end}]",
            )


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
