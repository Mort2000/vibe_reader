"""S4: long-context reading triggers chapter compaction."""

from __future__ import annotations

from vibe_verify.assertions import (
    check_agent_coverage,
    check_chat_prompt_context,
    check_chat_requests_without_selection,
    check_chat_response,
    check_chat_usage,
    check_compaction_summary_reused,
    extract_summary_text,
    fail,
)
from vibe_verify.scenario import ScenarioContext, ScenarioDefinition

from .common import (
    CORE_SUITES,
    DEFAULT_CORPUS_PURPOSE,
    S4_SCENARIO_ID,
    context_compacted,
    ensure_corpus,
    last_chat_paragraph,
    open_probe_chapter,
    policy_from_params,
    read_post_compaction_windows,
    read_until_context_compacted,
    scenario_api_interactions,
)


def s4_context_compaction() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S4_SCENARIO_ID,
        script=run_s4_context_compaction,
        suites=CORE_SUITES,
        corpus_purpose=DEFAULT_CORPUS_PURPOSE,
        description="S4: long-context reading triggers chapter compaction",
        post_checks=(check_s4_agent_evidence,),
    )


async def run_s4_context_compaction(context: ScenarioContext) -> None:
    ensure_corpus(context, S4_SCENARIO_ID)
    policy = policy_from_params(context)
    async with context.app.import_epub(context.params.corpus) as book:
        target = await open_probe_chapter(context, book)
        async with context.app.subscribe_events(
            book_id=book.id,
            chapter_idx=book.chapter_idx,
        ) as events:
            windows = await read_until_context_compacted(
                context,
                book,
                policy,
                events=events,
                target_paragraph=target,
            )
            if not context_compacted(context, book, events):
                fail("context compaction was not observed")
        if windows < policy.min_comment_windows:
            fail(
                "not enough comment windows before compaction",
                expected=policy.min_comment_windows,
                actual=windows,
            )
        if policy.post_compaction_comment_windows:
            await read_post_compaction_windows(
                context,
                book,
                policy,
            )
        chat_paragraph_idx = min(target, max(0, book.progress_paragraph_idx))
        response = await context.app.chat(
            book,
            paragraph_idx=chat_paragraph_idx,
            message=policy.chat_questions[0],
        )
        await context.user.wait_for_chat_response(response)
        check_chat_response(response)
        check_chat_usage(response)
        check_chat_requests_without_selection(
            scenario_api_interactions(context, S4_SCENARIO_ID)
        )


async def check_s4_agent_evidence(context: ScenarioContext) -> None:
    calls = context.llm.calls(scenario_id=S4_SCENARIO_ID)
    check_agent_coverage(
        calls,
        required_agents=(
            "ParagraphCommentAgent",
            "ContextCompactionAgent",
            "ReadingChatAgent",
        ),
    )
    compactions = [call for call in calls if call.agent == "ContextCompactionAgent"]
    summaries = [extract_summary_text(call.response) for call in compactions]
    if not any(summary.strip() for summary in summaries):
        fail("compaction summary text missing")
    chats = [call for call in calls if call.agent == "ReadingChatAgent"]
    check_chat_prompt_context(
        chats[-1],
        paragraph_idx=last_chat_paragraph(context, S4_SCENARIO_ID),
    )
    check_compaction_summary_reused(calls)
