"""S6: follow-up chat reuses recent session history."""

from __future__ import annotations

from vibe_verify.assertions import (
    check_chat_prompt_context,
    check_chat_requests_without_selection,
    check_chat_session_sequence,
    check_token_usage,
    extract_chat_response_text,
    fail,
)
from vibe_verify.scenario import ScenarioContext, ScenarioDefinition

from .common import (
    CORE_SUITES,
    DEFAULT_CORPUS_PURPOSE,
    S6_SCENARIO_ID,
    ask_chat_turns,
    ensure_corpus,
    expanded_chat_questions,
    policy_from_params,
    prepare_chat_position,
    probe_paragraph,
    s6_turn_count,
    scenario_api_interactions,
    trigger_new_context_compaction,
)


def s6_followup_chat() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S6_SCENARIO_ID,
        script=run_s6_followup_chat,
        suites=CORE_SUITES,
        corpus_purpose=DEFAULT_CORPUS_PURPOSE,
        description="S6: follow-up chat reuses recent session history",
        post_checks=(check_s6_chat_evidence,),
    )


async def run_s6_followup_chat(context: ScenarioContext) -> None:
    ensure_corpus(context, S6_SCENARIO_ID)
    policy = policy_from_params(context, min_chat_turns=2)
    async with context.app.import_epub(context.params.corpus) as book:
        target = await prepare_chat_position(context, book)
        turns = s6_turn_count(policy)
        responses = await ask_chat_turns(context, book, policy, target, turns=turns)
        check_chat_session_sequence(responses, minimum=turns)
        check_chat_requests_without_selection(
            scenario_api_interactions(context, S6_SCENARIO_ID),
            minimum=len(responses),
        )
        await trigger_new_context_compaction(
            context,
            book,
            policy,
            target,
            scenario_id=S6_SCENARIO_ID,
        )


async def check_s6_chat_evidence(context: ScenarioContext) -> None:
    check_chat_requests_without_selection(
        scenario_api_interactions(context, S6_SCENARIO_ID),
        minimum=2,
    )
    calls = context.llm.calls("ReadingChatAgent", scenario_id=S6_SCENARIO_ID)
    policy = policy_from_params(context, min_chat_turns=2)
    expected_turns = s6_turn_count(policy)
    if len(calls) < expected_turns:
        fail(
            "followup chat agent calls missing",
            expected=f">={expected_turns}",
            actual=len(calls),
        )
    for call in calls:
        check_token_usage(
            call.usage,
            allowed_sources={"estimate", "provider", "framework"},
        )
        check_chat_prompt_context(call, paragraph_idx=probe_paragraph(context))
    response_texts: list[str] = []
    for call in calls:
        response_text = extract_chat_response_text(call.response)
        if not response_text:
            fail(
                "chat response text missing from evidence",
                invocation_id=call.id,
            )
        response_texts.append(response_text)
    questions = expanded_chat_questions(policy, expected_turns)
    followup_prompt = calls[-1].prompt
    recent_question = questions[-2]
    if recent_question not in followup_prompt:
        fail("recent chat history missing from followup prompt")
    first_question = questions[0]
    if (
        expected_turns > policy.chat_history_recent_turns + 1
        and first_question in followup_prompt
    ):
        fail("stale chat history was not trimmed from followup prompt")

    previous_answer = response_texts[-2]
    if previous_answer not in followup_prompt:
        fail("previous assistant answer missing from followup prompt")

    items = context.llm.calls(scenario_id=S6_SCENARIO_ID)
    last_chat_index = max(
        index for index, item in enumerate(items) if item.agent == "ReadingChatAgent"
    )
    later_compactions = [
        item
        for index, item in enumerate(items)
        if item.agent == "ContextCompactionAgent" and index > last_chat_index
    ]
    if not later_compactions:
        fail("post-chat compaction evidence missing")
    forbidden_fragments = [
        *questions,
        *response_texts,
    ]
    for call in later_compactions:
        for fragment in forbidden_fragments:
            if fragment and fragment in call.prompt:
                fail(
                    "chat history leaked into compaction prompt",
                    invocation_id=call.id,
                )
