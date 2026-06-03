"""S3: fast scroll, jump-back, and latest progress merge."""

from __future__ import annotations

from vibe_verify.assertions import check_comments, check_window_covers, fail
from vibe_verify.scenario import ScenarioContext, ScenarioDefinition

from .common import (
    CORE_SUITES,
    DEFAULT_CORPUS_PURPOSE,
    S3_SCENARIO_ID,
    check_progress_at,
    ensure_corpus,
    fast_scroll_positions,
    open_probe_chapter,
    policy_from_params,
)


def s3_fast_scroll() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S3_SCENARIO_ID,
        script=run_s3_fast_scroll,
        suites=CORE_SUITES,
        corpus_purpose=DEFAULT_CORPUS_PURPOSE,
        description="S3: fast scroll, jump-back, and latest progress merge",
        post_checks=(check_s3_progress_merge_evidence,),
    )


async def run_s3_fast_scroll(context: ScenarioContext) -> None:
    ensure_corpus(context, S3_SCENARIO_ID)
    policy = policy_from_params(context)
    async with context.app.import_epub(context.params.corpus) as book:
        await open_probe_chapter(context, book)
        positions = fast_scroll_positions(book, policy)
        if not positions:
            fail("fast scroll requires at least one reachable paragraph")

        for offset, paragraph_idx in enumerate(positions):
            await context.user.fast_scroll_to(
                book,
                paragraph_idx,
                scroll_pct=min(1.0, 0.2 + offset * 0.2),
            )

        await check_progress_at(book, positions[-1])
        window = await book.wait_for_current_window_ready(
            context.user,
            timeout_s=policy.max_wait_comment_s,
        )
        check_window_covers(window, paragraph_idx=positions[-1])
        comments = await book.wait_for_comments(
            context.user,
            window.focus_start,
            window.focus_end,
            timeout_s=policy.max_wait_comment_s,
            required=False,
        )
        if comments:
            check_comments(
                comments,
                start=window.focus_start,
                end=window.focus_end,
                minimum=1,
            )

        backtrack = max(0, positions[-1] - 2)
        if backtrack < positions[-1]:
            await context.user.fast_scroll_to(book, backtrack, scroll_pct=0.0)
            await check_progress_at(book, backtrack)
            await context.user.fast_scroll_to(book, positions[-1], scroll_pct=1.0)
            await check_progress_at(book, positions[-1])


async def check_s3_progress_merge_evidence(context: ScenarioContext) -> None:
    progress_actions = [
        item
        for item in context.llm.hub.user_interactions
        if item.action == "fast_scroll_to"
        and item.correlation.scenario_id == S3_SCENARIO_ID
    ]
    calls = context.llm.calls("ParagraphCommentAgent", scenario_id=S3_SCENARIO_ID)
    if not calls:
        fail("fast scroll did not produce any comment agent evidence")
    job_like_calls = [call for call in calls if call.tool_calls or call.error]
    job_like_count = len(job_like_calls) if job_like_calls else len(calls)
    if len(progress_actions) >= 3 and job_like_count >= len(progress_actions):
        fail(
            "fast scroll progress was not merged before comment work",
            progress_actions=len(progress_actions),
            comment_jobs=job_like_count,
        )
