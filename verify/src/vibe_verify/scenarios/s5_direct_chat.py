"""S5: current-context direct chat without a selection."""

from __future__ import annotations

from vibe_verify.assertions import (
    check_agent_coverage,
    check_chat_prompt_context,
    check_chat_requests_without_selection,
    check_token_usage,
)
from vibe_verify.scenario import ScenarioContext, ScenarioDefinition

from .common import (
    CORE_SUITES,
    DEFAULT_CORPUS_PURPOSE,
    S5_SCENARIO_ID,
    ask_chat_turns,
    ensure_corpus,
    policy_from_params,
    prepare_chat_position,
    probe_paragraph,
    scenario_api_interactions,
)


def s5_direct_chat() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S5_SCENARIO_ID,
        script=run_s5_direct_chat,
        suites=CORE_SUITES,
        corpus_purpose=DEFAULT_CORPUS_PURPOSE,
        description="S5: current-context direct chat without a selection",
        post_checks=(check_s5_chat_evidence,),
    )


async def run_s5_direct_chat(context: ScenarioContext) -> None:
    ensure_corpus(context, S5_SCENARIO_ID)
    policy = policy_from_params(context, min_chat_turns=1)
    async with context.app.import_epub(context.params.corpus) as book:
        target = await prepare_chat_position(context, book)
        responses = await ask_chat_turns(context, book, policy, target, turns=1)
        check_chat_requests_without_selection(
            scenario_api_interactions(context, S5_SCENARIO_ID),
            minimum=len(responses),
        )


async def check_s5_chat_evidence(context: ScenarioContext) -> None:
    check_chat_requests_without_selection(
        scenario_api_interactions(context, S5_SCENARIO_ID)
    )
    calls = context.llm.calls("ReadingChatAgent", scenario_id=S5_SCENARIO_ID)
    check_agent_coverage(calls, required_agents=("ReadingChatAgent",))
    check_chat_prompt_context(calls[-1], paragraph_idx=probe_paragraph(context))
    check_token_usage(
        calls[-1].usage,
        allowed_sources={"estimate", "provider", "framework"},
    )
