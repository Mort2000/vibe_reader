"""S2: continuous reading produces valid paragraph comments."""

from __future__ import annotations

from vibe_verify.assertions import check_agent_coverage
from vibe_verify.scenario import ScenarioContext, ScenarioDefinition

from .common import (
    CORE_SUITES,
    DEFAULT_CORPUS_PURPOSE,
    S2_SCENARIO_ID,
    ensure_corpus,
    open_probe_chapter,
    policy_from_params,
    read_comment_windows,
)


def s2_continuous_reading_comments() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S2_SCENARIO_ID,
        script=run_s2_continuous_reading_comments,
        suites=CORE_SUITES,
        corpus_purpose=DEFAULT_CORPUS_PURPOSE,
        description="S2: continuous reading produces valid paragraph comments",
        post_checks=(check_s2_agent_evidence,),
    )


async def run_s2_continuous_reading_comments(context: ScenarioContext) -> None:
    ensure_corpus(context, S2_SCENARIO_ID)
    policy = policy_from_params(context)
    async with context.app.import_epub(context.params.corpus) as book:
        await open_probe_chapter(context, book)
        await read_comment_windows(context, book, policy, policy.min_comment_windows)


async def check_s2_agent_evidence(context: ScenarioContext) -> None:
    calls = context.llm.calls(scenario_id=S2_SCENARIO_ID)
    check_agent_coverage(calls, required_agents=("ParagraphCommentAgent",))
