"""Compaction polling, advancement, and post-compaction windows."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from ..core.client_factory import TargetClient
from ..core.context import ScenarioContext
from ..core.config import VerifyConfig
from ..assertions.context import (
    assert_chapter_summary_in_subsequent_context,
    assert_compaction_completed,
    assert_compaction_failure_does_not_block_comments,
    assert_compaction_source_scale,
    assert_l2_chunk_boundaries_stable,
    assert_s4_context_evidence,
    assert_token_budget,
    extract_l2_chunks,
    find_chapter_summary_component,
    find_compaction_agent_runs,
    find_comment_agent_runs,
    record_context_metrics_from_verify,
    select_post_compaction_comment_runs,
)
from ..compaction_audit import CompactionAuditExporter
from ..metrics_collector import MetricsAggregator
from ..core.scenario import StepAssertionError, assert_that
from ..sse_collector import SSEEvent

from .audit import (
    append_compaction_jobs_audit,
    collect_latest_injected_contexts,
    fetch_verify_agent_runs,
    fetch_verify_jobs,
    filter_agent_runs_for_chapter,
)
from .metrics import sync_real_llm_tracker_from_verify_metrics
from .comments import drain_chapter_comment_jobs, wait_for_window_done
from .corpus import chapter_by_idx, last_paragraph_idx
from .reading import (
    ReadingCursor,
    ReadingSession,
    ReadingTrace,
    _cross_reading_chapter,
    advance_reading,
    advance_reading_to,
)

COMPACTION_NOOP_FAIL_FAST_S = 30.0


async def _poll_compaction_verify_jobs(
    client: TargetClient,
    book_id: int,
    chapter_idx: int,
    *,
    run_id: str | None,
    min_job_id: int = 0,
) -> list[dict[str, Any]]:
    jobs = await fetch_verify_jobs(
        client,
        book_id,
        chapter_idx,
        job_type="compact_context",
        run_id=run_id,
    )
    if not jobs:
        jobs = await fetch_verify_jobs(
            client,
            book_id,
            chapter_idx,
            job_type="compact_context",
        )
    return _filter_compaction_jobs(jobs, min_job_id=min_job_id)


def _job_id(job: dict[str, Any]) -> int:
    return int(job.get("id") or job.get("job_id") or 0)


def _filter_compaction_jobs(
    jobs: list[dict[str, Any]], *, min_job_id: int = 0
) -> list[dict[str, Any]]:
    if min_job_id <= 0:
        return jobs
    return [job for job in jobs if _job_id(job) > min_job_id]


@dataclass
class CompactionNoopTracker:
    """Fail fast when done compaction jobs lack agent-run evidence."""

    first_seen_at: float | None = None

    def check(
        self,
        *,
        done_job: dict[str, Any] | None,
        has_agent_run: bool,
        scenario_id: str,
        jobs: list[dict[str, Any]] | None = None,
    ) -> None:
        if done_job and not has_agent_run:
            now = time.monotonic()
            if self.first_seen_at is None:
                self.first_seen_at = now
                return
            if now - self.first_seen_at >= COMPACTION_NOOP_FAIL_FAST_S:
                raise StepAssertionError(
                    assertion="compaction_noop_done",
                    message=(
                        "Compaction job marked done but ContextCompactionAgent run "
                        f"missing evidence for {COMPACTION_NOOP_FAIL_FAST_S:.0f}s"
                    ),
                    expected="compaction agent run with summary or token usage",
                    actual={
                        "scenario_id": scenario_id,
                        "done_job": done_job,
                        "jobs_seen": len(jobs or []),
                    },
                )
            return
        self.first_seen_at = None


async def _compaction_has_agent_run(
    client: TargetClient,
    book_id: int,
    chapter_idx: int,
    run_id: str | None,
    *,
    scenario_id: str | None,
    min_job_id: int,
) -> bool:
    return (
        await _poll_real_compaction_agent_job(
            client,
            book_id,
            chapter_idx,
            run_id,
            scenario_id=scenario_id,
            min_job_id=min_job_id,
        )
        is not None
    )


async def _poll_real_compaction_agent_job(
    client: TargetClient,
    book_id: int,
    chapter_idx: int,
    run_id: str | None,
    *,
    scenario_id: str | None = None,
    min_job_id: int = 0,
) -> dict[str, Any] | None:
    if not run_id:
        return None
    from ..assertions.context import extract_chapter_summary, find_compaction_agent_runs

    agent_runs = await fetch_verify_agent_runs(client, run_id)
    for run in reversed(find_compaction_agent_runs(agent_runs)):
        interaction = run.get("interaction") or run
        run_book_id = int(run.get("book_id") or interaction.get("book_id") or 0)
        run_chapter_idx = int(
            run.get("chapter_idx")
            if run.get("chapter_idx") is not None
            else interaction.get("chapter_idx")
            if interaction.get("chapter_idx") is not None
            else -1
        )
        if run_book_id != book_id or run_chapter_idx != chapter_idx:
            continue
        job_id = int(run.get("job_id") or interaction.get("job_id") or 0)
        if job_id <= min_job_id:
            continue
        if scenario_id:
            run_scenario = run.get("verify_scenario_id") or interaction.get(
                "verify_scenario_id"
            ) or interaction.get("scenario_id")
            if run_scenario and run_scenario != scenario_id:
                continue
        usage = interaction.get("usage") or {}
        input_tokens = int(
            run.get("input_tokens")
            or interaction.get("input_tokens")
            or usage.get("input_tokens")
            or 0
        )
        if input_tokens > 0:
            return {
                "id": run.get("job_id") or interaction.get("job_id"),
                "job_type": "compact_context",
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "status": "done",
                "trace_id": run.get("trace_id"),
            }
        if (
            interaction.get("summary_id")
            or interaction.get("next_summary")
            or extract_chapter_summary(interaction)
        ):
            return {
                "id": run.get("job_id") or interaction.get("job_id"),
                "job_type": "compact_context",
                "book_id": book_id,
                "chapter_idx": chapter_idx,
                "status": "done",
                "trace_id": run.get("trace_id"),
            }
    return None


async def wait_for_compaction(
    client: TargetClient,
    session: ReadingSession,
    book_id: int,
    chapter_idx: int,
    timeout_s: float,
    trace: ReadingTrace,
    *,
    run_id: str | None = None,
    scenario_id: str | None = None,
    min_job_id: int = 0,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Wait for compaction completion.

    Returns ``(done_job, jobs, failed_job)``. Does not raise on failure; callers
    decide whether a failed compaction is a hard failure.
    """

    def _chapter_filter(evt: SSEEvent) -> bool:
        return evt.book_id == book_id and evt.chapter_idx == chapter_idx

    deadline = time.monotonic() + timeout_s
    last_failed: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        jobs = await _poll_compaction_verify_jobs(
            client, book_id, chapter_idx, run_id=run_id, min_job_id=min_job_id
        )
        failed_jobs = [job for job in jobs if job.get("status") == "failed"]
        if failed_jobs:
            last_failed = failed_jobs[-1]

        real_run = await _poll_real_compaction_agent_job(
            client,
            book_id,
            chapter_idx,
            run_id,
            scenario_id=scenario_id,
            min_job_id=min_job_id,
        )
        if real_run:
            session.ingest_events(trace)
            return real_run, jobs, last_failed

        done_jobs = [
            job
            for job in jobs
            if job.get("status") == "done" and _job_id(job) > min_job_id
        ]
        if done_jobs and not run_id:
            session.ingest_events(trace)
            return done_jobs[-1], jobs, last_failed

        remaining = deadline - time.monotonic()
        if remaining > 0:
            evt = await session.collector.wait_for_event(
                "context.compacted",
                timeout_s=min(remaining, 2.0),
                predicate=_chapter_filter,
            )
            if evt:
                session.ingest_events(trace)
                jobs = await _poll_compaction_verify_jobs(
                    client,
                    book_id,
                    chapter_idx,
                    run_id=run_id,
                    min_job_id=min_job_id,
                )
                real_run = await _poll_real_compaction_agent_job(
                    client,
                    book_id,
                    chapter_idx,
                    run_id,
                    scenario_id=scenario_id,
                    min_job_id=min_job_id,
                )
                if real_run:
                    return real_run, jobs, last_failed
                done_jobs = [
                    job
                    for job in jobs
                    if job.get("status") == "done" and _job_id(job) > min_job_id
                ]
                if done_jobs:
                    return done_jobs[-1], jobs, last_failed
                failed_jobs = [job for job in jobs if job.get("status") == "failed"]
                if failed_jobs:
                    last_failed = failed_jobs[-1]
                return None, jobs, last_failed

        await asyncio.sleep(0.5)

    session.ingest_events(trace)
    return (
        None,
        await _poll_compaction_verify_jobs(
            client,
            book_id,
            chapter_idx,
            run_id=run_id,
            min_job_id=min_job_id,
        ),
        last_failed,
    )


async def advance_until_compaction(
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
    batch_size: int | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Advance reading until compaction completes on the start chapter.

    Reads forward from *cursor*, crossing chapter boundaries when the current
    chapter ends without compaction yet.
    """
    if batch_size is None:
        batch_size = config.params.compaction_advance_batch_size

    compaction_chapter_idx = cursor.chapter_idx
    ctx.compaction_chapter_idx = compaction_chapter_idx
    active_session = session

    chapter = chapter_by_idx(chapters, cursor.chapter_idx)
    if chapter is None:
        raise StepAssertionError(
            assertion="chapter_exists",
            message=f"Chapter {cursor.chapter_idx} not found",
            actual={"chapter_idx": cursor.chapter_idx},
        )

    chapter_last = last_paragraph_idx(chapter)
    next_paragraph = cursor.paragraph_idx
    timeout_s = float(config.params.max_wait_compaction_s)
    deadline = time.monotonic() + timeout_s
    last_job: dict[str, Any] | None = None
    last_failed: dict[str, Any] | None = None
    all_jobs: list[dict[str, Any]] = []
    baseline_jobs = await _poll_compaction_verify_jobs(
        client,
        book_id,
        compaction_chapter_idx,
        run_id=ctx.run_manager.run_id,
    )
    min_job_id = max((_job_id(job) for job in baseline_jobs), default=0)
    noop_tracker = CompactionNoopTracker()
    run_id = ctx.run_manager.run_id

    prior_run = await _poll_real_compaction_agent_job(
        client,
        book_id,
        compaction_chapter_idx,
        run_id,
        scenario_id=None,
        min_job_id=0,
    )
    if prior_run and _job_id(prior_run) <= min_job_id:
        session.ingest_events(trace)
        append_compaction_jobs_audit(
            ctx.run_manager,
            baseline_jobs,
            scenario_id=scenario_id,
            min_job_id=min_job_id,
        )
        return prior_run, baseline_jobs, None

    while time.monotonic() < deadline:
        done_job, jobs, failed_job = await wait_for_compaction(
            client,
            active_session,
            book_id,
            compaction_chapter_idx,
            timeout_s=min(2.0, deadline - time.monotonic()),
            trace=trace,
            run_id=run_id,
            scenario_id=scenario_id,
            min_job_id=min_job_id,
        )
        all_jobs = jobs or all_jobs
        if failed_job:
            last_failed = failed_job
        if done_job:
            has_agent_run = await _compaction_has_agent_run(
                client,
                book_id,
                compaction_chapter_idx,
                run_id,
                scenario_id=scenario_id,
                min_job_id=min_job_id,
            )
            noop_tracker.check(
                done_job=done_job,
                has_agent_run=has_agent_run,
                scenario_id=scenario_id,
                jobs=jobs,
            )
            if has_agent_run or not run_id:
                cursor.paragraph_idx = max(cursor.paragraph_idx, next_paragraph)
                ctx.final_paragraph_idx = cursor.paragraph_idx
                ctx.reading_session = active_session
                append_compaction_jobs_audit(
                    ctx.run_manager,
                    all_jobs,
                    scenario_id=scenario_id,
                    min_job_id=min_job_id,
                )
                return done_job, all_jobs, last_failed
        if jobs:
            last_job = jobs[-1]

        if next_paragraph > chapter_last:
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
            ctx.chapter_idx = cursor.chapter_idx
            ctx.reading_session = active_session
            chapter = chapter_by_idx(chapters, cursor.chapter_idx)
            if chapter is None:
                break
            chapter_last = last_paragraph_idx(chapter)
            next_paragraph = cursor.paragraph_idx
            continue

        end = min(next_paragraph + batch_size, chapter_last)
        last = await advance_reading(
            client,
            ctx,
            book_id,
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
        next_paragraph = last + 1

    ctx.reading_session = active_session
    append_compaction_jobs_audit(
        ctx.run_manager,
        all_jobs,
        scenario_id=scenario_id,
        min_job_id=min_job_id,
    )
    return last_job, all_jobs, last_failed


async def _advance_post_compaction_comment_windows(
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
    post_comment_windows: int,
) -> tuple[int, ReadingSession]:
    """Re-read after compaction so new comment windows include chapter summary."""
    post_batch = max(12, config.params.read_batch_size // 4)
    max_wait = float(config.params.max_wait_comment_window_s)
    completed_post = 0
    active_session = session
    chapter_rewind_done = False

    while completed_post < post_comment_windows:
        chapter = chapter_by_idx(chapters, cursor.chapter_idx)
        if chapter is None:
            break
        chapter_last = last_paragraph_idx(chapter)

        if cursor.paragraph_idx >= chapter_last and not chapter_rewind_done:
            rewind = min(
                max(post_batch, config.params.read_batch_size),
                chapter_last,
            )
            back = max(0, chapter_last - rewind)
            if back < chapter_last:
                await advance_reading_to(
                    client,
                    ctx,
                    book_id,
                    cursor.chapter_idx,
                    back,
                    trace,
                    scenario_id=scenario_id,
                    step_id=step_id,
                    metrics=metrics,
                )
                cursor.paragraph_idx = back
                chapter_rewind_done = True
                continue
            chapter_rewind_done = True

        if cursor.paragraph_idx >= chapter_last:
            active_session, moved = await _cross_reading_chapter(
                ctx,
                cursor,
                chapters,
                active_session,
                scenario_id=scenario_id,
                book_id=book_id,
            )
            if moved:
                ctx.extras["chapters_crossed"] = int(ctx.extras.get("chapters_crossed") or 0) + 1
                chapter_rewind_done = False
                await advance_reading_to(
                    client,
                    ctx,
                    book_id,
                    cursor.chapter_idx,
                    0,
                    trace,
                    scenario_id=scenario_id,
                    step_id=step_id,
                    metrics=metrics,
                )
                continue
            break

        end = min(cursor.paragraph_idx + post_batch, chapter_last)
        if end <= cursor.paragraph_idx:
            break
        last = await advance_reading_to(
            client,
            ctx,
            book_id,
            cursor.chapter_idx,
            end,
            trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=metrics,
        )
        cursor.paragraph_idx = last
        ctx.final_paragraph_idx = last
        window = await wait_for_window_done(
            client,
            active_session,
            book_id,
            cursor.chapter_idx,
            last,
            max_wait,
            trace,
            retry_on_failure=True,
        )
        if window and window.get("status") == "done":
            completed_post += 1

    compaction_chapter_idx = ctx.long_chapter_idx
    if compaction_chapter_idx is not None:
        chapter = chapter_by_idx(chapters, int(compaction_chapter_idx))
        if chapter and cursor.chapter_idx == int(compaction_chapter_idx):
            chapter_last = last_paragraph_idx(chapter)
            if cursor.paragraph_idx < chapter_last:
                last = await advance_reading_to(
                    client,
                    ctx,
                    book_id,
                    cursor.chapter_idx,
                    chapter_last,
                    trace,
                    scenario_id=scenario_id,
                    step_id=step_id,
                    metrics=metrics,
                )
                cursor.paragraph_idx = last
                ctx.final_paragraph_idx = last
                await wait_for_window_done(
                    client,
                    active_session,
                    book_id,
                    cursor.chapter_idx,
                    last,
                    max_wait,
                    trace,
                    retry_on_failure=True,
                )
    return completed_post, active_session


async def advance_until_compaction_then_post_windows(
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
    batch_size: int | None = None,
    post_comment_windows: int | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Advance with large batches until compaction, then finish a few comment windows."""
    if batch_size is None:
        batch_size = config.params.compaction_advance_batch_size
    if post_comment_windows is None:
        post_comment_windows = config.params.long_flow.post_compaction_comment_windows

    chapter = chapter_by_idx(chapters, cursor.chapter_idx)
    if chapter is None:
        raise StepAssertionError(
            assertion="chapter_exists",
            message=f"Chapter {cursor.chapter_idx} not found",
            actual={"chapter_idx": cursor.chapter_idx},
        )

    chapter_last = last_paragraph_idx(chapter)
    next_paragraph = cursor.paragraph_idx
    if next_paragraph == 0:
        await advance_reading_to(
            client,
            ctx,
            book_id,
            cursor.chapter_idx,
            0,
            trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=metrics,
        )
    timeout_s = float(config.params.max_wait_compaction_s)
    deadline = time.monotonic() + timeout_s
    max_wait = float(config.params.max_wait_comment_window_s)
    last_failed: dict[str, Any] | None = None
    all_jobs: list[dict[str, Any]] = []
    done_job: dict[str, Any] | None = None
    chapter_end_nudged = False
    run_id = ctx.run_manager.run_id
    baseline_jobs = await _poll_compaction_verify_jobs(
        client,
        book_id,
        cursor.chapter_idx,
        run_id=run_id,
    )
    min_job_id = max((_job_id(job) for job in baseline_jobs), default=0)
    noop_tracker = CompactionNoopTracker()

    while time.monotonic() < deadline:
        done_job, jobs, failed_job = await wait_for_compaction(
            client,
            session,
            book_id,
            cursor.chapter_idx,
            timeout_s=min(2.0, deadline - time.monotonic()),
            trace=trace,
            run_id=run_id,
            scenario_id=scenario_id,
            min_job_id=min_job_id,
        )
        all_jobs = jobs or all_jobs
        if failed_job:
            last_failed = failed_job
            # R1 A3 happy path: compaction must succeed before post-compaction windows.
            # Contrast advance_until_compaction (S4), which records failure and lets the
            # scenario assert comments remain observable despite compaction failure.
            raise StepAssertionError(
                assertion="compaction_job_failed",
                message="Compaction job failed during advance_until_compaction_then_post_windows",
                expected="done compaction job",
                actual=failed_job,
            )
        if done_job:
            has_agent_run = await _compaction_has_agent_run(
                client,
                book_id,
                cursor.chapter_idx,
                run_id,
                scenario_id=scenario_id,
                min_job_id=min_job_id,
            )
            noop_tracker.check(
                done_job=done_job,
                has_agent_run=has_agent_run,
                scenario_id=scenario_id,
                jobs=jobs,
            )
            if has_agent_run or not run_id:
                break

        if next_paragraph <= chapter_last:
            end = min(next_paragraph + batch_size - 1, chapter_last)
            last = await advance_reading_to(
                client,
                ctx,
                book_id,
                cursor.chapter_idx,
                end,
                trace,
                scenario_id=scenario_id,
                step_id=step_id,
                metrics=metrics,
            )
            cursor.paragraph_idx = last
            ctx.final_paragraph_idx = last
            next_paragraph = last + 1
            await wait_for_window_done(
                client,
                session,
                book_id,
                cursor.chapter_idx,
                last,
                max_wait,
                trace,
                retry_on_failure=True,
            )
        else:
            if not chapter_end_nudged:
                await advance_reading_to(
                    client,
                    ctx,
                    book_id,
                    cursor.chapter_idx,
                    chapter_last,
                    trace,
                    scenario_id=scenario_id,
                    step_id=step_id,
                    metrics=metrics,
                )
                cursor.paragraph_idx = chapter_last
                ctx.final_paragraph_idx = chapter_last
                chapter_end_nudged = True
            await drain_chapter_comment_jobs(
                client,
                session,
                book_id,
                cursor.chapter_idx,
                chapter_last,
                trace,
                scenario_id=scenario_id,
                step_id=step_id,
                config=config,
                timeout_s=min(60.0, deadline - time.monotonic()),
            )
            await asyncio.sleep(1.0)

    if not done_job:
        raise StepAssertionError(
            assertion="compaction_triggered",
            message="Timed out before the first compaction completed",
            actual={
                "chapter_idx": cursor.chapter_idx,
                "paragraph_idx": cursor.paragraph_idx,
                "jobs_seen": len(all_jobs),
                "last_failed": last_failed,
            },
        )

    completed_post, session = await _advance_post_compaction_comment_windows(
        client,
        ctx,
        book_id,
        cursor,
        chapters,
        trace,
        session,
        scenario_id=scenario_id,
        step_id=step_id,
        metrics=metrics,
        config=config,
        post_comment_windows=post_comment_windows,
    )
    ctx.post_compaction_comment_windows_completed = completed_post
    ctx.reading_session = session
    append_compaction_jobs_audit(
        ctx.run_manager,
        all_jobs,
        scenario_id=scenario_id,
        min_job_id=min_job_id,
    )
    return done_job, all_jobs, last_failed


async def advance_for_a3_compaction(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "advance_for_compaction",
) -> None:
    """Advance until compaction completes, then validate post-compaction comments."""
    config = ctx.config
    metrics = ctx.metrics
    trace = ctx.reading_trace
    session = ctx.reading_session
    assert session is not None
    cursor = ctx.cursor
    assert isinstance(cursor, ReadingCursor)
    assert ctx.book_id is not None
    chapters: list[dict[str, Any]] = ctx.chapters or []
    long_flow = config.params.long_flow
    probe = ctx.probe

    async with TargetClient(
        config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        (
            done_job,
            compaction_jobs,
            failed_job,
        ) = await advance_until_compaction_then_post_windows(
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

        if failed_job and not done_job:
            raise StepAssertionError(
                assertion="compaction_job_failed",
                message="Real compaction job failed before completion",
                expected="done",
                actual=failed_job,
            )

        agent_runs = await fetch_verify_agent_runs(
            client,
            ctx.run_manager.run_id,
            scenario_id=scenario_id,
        )
        compaction_runs = find_compaction_agent_runs(agent_runs)
        assert_compaction_completed(
            compaction_jobs=compaction_jobs,
            compaction_runs=compaction_runs,
            require_real=config.params.assertions.require_compaction_audit_real,
        )

        min_tokens = (
            probe.test_compaction_min_source_tokens
            or long_flow.test_compaction_min_source_tokens
        )
        min_paragraphs = (
            probe.test_compaction_min_source_paragraphs
            or long_flow.test_compaction_min_source_paragraphs
        )
        if compaction_runs:
            assert_compaction_source_scale(
                compaction_runs[-1],
                min_source_tokens=min_tokens,
                min_source_paragraphs=min_paragraphs,
            )
            interaction = compaction_runs[-1].get("interaction") or compaction_runs[-1]
            assert_token_budget(interaction.get("injected_context") or {}, config)

        compaction_job_id = int((done_job or {}).get("id") or 0)
        compaction_trace_ids = {
            str(run.get("trace_id") or "")
            for run in compaction_runs
            if run.get("trace_id")
        }

        assert_that.gte(
            ctx.post_compaction_comment_windows_completed,
            config.params.long_flow.post_compaction_comment_windows,
            label="post_compaction_comment_windows_completed",
        )

        agent_runs_after = await fetch_verify_agent_runs(
            client,
            ctx.run_manager.run_id,
            scenario_id=scenario_id,
        )
        comment_runs_after = find_comment_agent_runs(agent_runs_after)
        compaction_chapter = int(ctx.long_chapter_idx or cursor.chapter_idx)
        post_compaction_comments = select_post_compaction_comment_runs(
            comment_runs_after,
            compaction_job_id=compaction_job_id or None,
            compaction_trace_ids=compaction_trace_ids,
            compaction_chapter_idx=compaction_chapter,
        )
        assert_that.is_true(
            len(post_compaction_comments) > 0,
            "Expected a post-compaction ParagraphCommentAgent run for summary injection check",
        )
        post_run = None
        for candidate in reversed(post_compaction_comments):
            if int(candidate.get("chapter_idx") or 0) != compaction_chapter:
                continue
            injected = (candidate.get("interaction") or candidate).get(
                "injected_context"
            ) or {}
            if find_chapter_summary_component(injected):
                post_run = candidate
                break
        if post_run is None:
            post_run = post_compaction_comments[-1]
        post_injected = (post_run.get("interaction") or post_run).get(
            "injected_context"
        ) or {}
        assert_chapter_summary_in_subsequent_context(
            post_injected,
            compaction_run=compaction_runs[-1] if compaction_runs else None,
        )
        ctx.post_compaction_comment_run = post_run

        verify_metrics = await sync_real_llm_tracker_from_verify_metrics(
            client,
            ctx.run_manager,
            config,
            scenario_id=scenario_id,
        )
        if verify_metrics:
            record_context_metrics_from_verify(
                metrics,
                verify_metrics,
                scenario_id=scenario_id,
                step_id=step_id,
            )
    ctx.run_manager.real_llm_tracker.phase_coverage["A3_compaction"] = True
    ctx.compaction_job = done_job
    ctx.compaction_jobs = compaction_jobs
    ctx.compaction_failed_job = failed_job
    ctx.compaction_agent_runs = compaction_runs
    ctx.final_paragraph_idx = cursor.paragraph_idx


def _enrich_contexts_from_comment_runs(
    contexts: list[dict[str, Any]],
    comment_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill sparse injected contexts from comment agent runs when verify API is limited."""
    enriched = list(contexts)
    if len(enriched) >= 2 or not comment_runs:
        return enriched
    first_ctx = (comment_runs[0].get("interaction") or comment_runs[0]).get(
        "injected_context"
    )
    last_ctx = (comment_runs[-1].get("interaction") or comment_runs[-1]).get(
        "injected_context"
    )
    if isinstance(first_ctx, dict):
        enriched.insert(0, first_ctx)
    if isinstance(last_ctx, dict) and last_ctx not in enriched:
        enriched.append(last_ctx)
    return enriched


def _record_l2_manifest_snapshots(
    exporter: CompactionAuditExporter,
    contexts: list[dict[str, Any]],
    cursor: ReadingCursor,
    *,
    scenario_id: str,
    step_id: str,
) -> None:
    if len(contexts) < 2:
        return
    assert_l2_chunk_boundaries_stable(
        extract_l2_chunks(contexts[0]),
        extract_l2_chunks(contexts[-1]),
    )
    exporter.add_l2_manifest(
        scenario_id=scenario_id,
        step_id=step_id,
        chapter_idx=cursor.chapter_idx,
        paragraph_idx=cursor.paragraph_idx,
        injected_context=contexts[0],
    )
    exporter.add_l2_manifest(
        scenario_id=scenario_id,
        step_id=f"{step_id}_final",
        chapter_idx=cursor.chapter_idx,
        paragraph_idx=cursor.paragraph_idx,
        injected_context=contexts[-1],
    )


async def advance_for_s4_long_context(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "advance_reading",
) -> None:
    """Advance until compaction, validate L2 chunk stability, and record evidence."""
    config = ctx.config
    metrics = ctx.metrics
    trace = ctx.reading_trace
    session = ctx.reading_session
    assert session is not None
    cursor = ctx.cursor
    assert isinstance(cursor, ReadingCursor)
    assert ctx.book_id is not None
    chapters: list[dict[str, Any]] = ctx.chapters or []
    exporter: CompactionAuditExporter = ctx.compaction_audit_exporter

    async with TargetClient(
        config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        done_job, compaction_jobs, failed_job = await advance_until_compaction(
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

        all_agent_runs = await fetch_verify_agent_runs(
            client,
            ctx.run_manager.run_id,
        )
        s4_agent_runs = await fetch_verify_agent_runs(
            client,
            ctx.run_manager.run_id,
            scenario_id=scenario_id,
        )
        compaction_chapter_idx = int(
            ctx.compaction_chapter_idx or cursor.chapter_idx
        )
        chapter_agent_runs = filter_agent_runs_for_chapter(
            all_agent_runs,
            compaction_chapter_idx,
        )

        comment_runs = find_comment_agent_runs(s4_agent_runs)
        compaction_runs = find_compaction_agent_runs(chapter_agent_runs)
        contexts = await collect_latest_injected_contexts(
            client,
            ctx.run_manager,
            scenario_id=scenario_id,
        )
        contexts = _enrich_contexts_from_comment_runs(contexts, comment_runs)
        _record_l2_manifest_snapshots(
            exporter,
            contexts,
            cursor,
            scenario_id=scenario_id,
            step_id=step_id,
        )

        for injected in contexts:
            assert_token_budget(injected, config)

        assert_compaction_failure_does_not_block_comments(
            comment_runs=comment_runs,
            trace=trace,
            failed_job=failed_job,
        )
        assert_compaction_completed(
            compaction_jobs=compaction_jobs,
            compaction_runs=compaction_runs,
            require_agent_run=True,
        )
    ctx.compaction_job = done_job
    ctx.compaction_jobs = compaction_jobs
    ctx.compaction_failed_job = failed_job
    ctx.compaction_agent_runs = compaction_runs
    ctx.comment_agent_runs = comment_runs
    ctx.injected_contexts = contexts
    ctx.final_paragraph_idx = cursor.paragraph_idx


async def advance_s4_long_context(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "advance_reading",
) -> None:
    """Advance S4 reading until compaction completes and record L2 evidence."""
    await advance_for_s4_long_context(
        ctx,
        scenario_id=scenario_id,
        step_id=step_id,
    )


async def verify_s4_context(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "verify_context",
) -> None:
    """Validate S4 token budgets, compaction observation, and L2 chunk stability."""
    assert_s4_context_evidence(
        config=ctx.config,
        injected_contexts=ctx.injected_contexts,
        compaction_jobs=ctx.compaction_jobs,
        compaction_runs=ctx.compaction_agent_runs,
        completed_compactions=ctx.reading_trace.completed_compactions,
    )
