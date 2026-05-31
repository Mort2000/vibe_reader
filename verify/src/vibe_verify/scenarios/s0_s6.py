"""Independent S0-S6 verification scenarios."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from vibe_verify.assertions import (
    check_agent_coverage,
    check_available_count,
    check_chat_requests_without_selection,
    check_chat_response,
    check_chat_usage,
    check_comments,
    check_compaction_summary_reused,
    check_followup_session,
    check_token_usage,
    extract_summary_text,
    fail,
)
from vibe_verify.driver import BookFacade, ChatResponse
from vibe_verify.scenario import ScenarioContext, ScenarioDefinition
from vibe_verify.scenarios.r1_full_flow import (
    context_compacted,
    read_post_compaction_windows,
    read_until_context_compacted,
)

DEFAULT_CORPUS_PURPOSE = "happy_path_current"

S0_SCENARIO_ID = "S0_environment_connectivity"
S1_SCENARIO_ID = "S1_import_book"
S2_SCENARIO_ID = "S2_continuous_reading_comments"
S3_SCENARIO_ID = "S3_fast_scroll"
S4_SCENARIO_ID = "S4_context_compaction"
S5_SCENARIO_ID = "S5_direct_chat"
S6_SCENARIO_ID = "S6_followup_chat"

CORE_SUITES = frozenset({"core", "a4"})


@dataclass(frozen=True)
class ScenarioPolicy:
    read_batch_size: int = 4
    read_batches: int = 6
    min_comment_windows: int = 1
    post_compaction_comment_windows: int = 0
    min_chat_turns: int = 1
    max_wait_comment_s: float = 60.0
    max_wait_compaction_s: float = 120.0
    fast_scroll_offsets: tuple[int, ...] = (1, 2, 4, 6)
    chat_history_recent_turns: int = 6
    s6_chat_turns: int = 8
    chat_questions: tuple[str, ...] = (
        "裁剪哨兵 Alpha：请概括当前阅读位置的主要矛盾。",
        "请指出上一轮主要矛盾在当前上下文中的依据。",
        "把当前阅读位置的人物行动压缩成一句话。",
        "这个场景的情绪压力来自哪里？",
        "只基于当前上下文，给我一个不剧透的阅读提示。",
        "上一条提示里最重要的词是什么？",
        "请继续沿用刚才的说法，补充一个细节。",
        "最终追问：结合最近对话，给出一句短评。",
    )


def s0_environment_connectivity() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S0_SCENARIO_ID,
        script=run_s0_environment_connectivity,
        suites=CORE_SUITES,
        description="S0: verify mode runtime and LLM connectivity",
    )


def s1_import_book() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S1_SCENARIO_ID,
        script=run_s1_import_book,
        suites=CORE_SUITES,
        corpus_purpose=DEFAULT_CORPUS_PURPOSE,
        description="S1: import an authorized book through the public API",
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


def s3_fast_scroll() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S3_SCENARIO_ID,
        script=run_s3_fast_scroll,
        suites=CORE_SUITES,
        corpus_purpose=DEFAULT_CORPUS_PURPOSE,
        description="S3: fast scroll, jump-back, and latest progress merge",
        post_checks=(check_s3_progress_merge_evidence,),
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


def s5_direct_chat() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S5_SCENARIO_ID,
        script=run_s5_direct_chat,
        suites=CORE_SUITES,
        corpus_purpose=DEFAULT_CORPUS_PURPOSE,
        description="S5: current-context direct chat without a selection",
        post_checks=(check_s5_chat_evidence,),
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


def s0_to_s6_scenarios() -> tuple[ScenarioDefinition, ...]:
    return (
        s0_environment_connectivity(),
        s1_import_book(),
        s2_continuous_reading_comments(),
        s3_fast_scroll(),
        s4_context_compaction(),
        s5_direct_chat(),
        s6_followup_chat(),
    )


async def run_s0_environment_connectivity(context: ScenarioContext) -> None:
    runtime = await context.observability.runtime()
    if runtime.get("verify_mode") is not True:
        fail("backend verify mode is not enabled", actual=runtime.get("verify_mode"))
    llm = runtime.get("llm")
    if not isinstance(llm, dict) or llm.get("base_url_configured") is not True:
        fail("backend LLM base URL is not configured", actual=llm)

    ping = await context.observability.llm_ping()
    if ping.get("ok") is not True:
        fail("LLM ping failed", actual=ping)
    if not str(ping.get("model", "")).strip():
        fail("LLM ping model missing")
    tokens = ping.get("tokens")
    if not isinstance(tokens, dict):
        fail("LLM ping tokens missing", actual=tokens)


async def run_s1_import_book(context: ScenarioContext) -> None:
    ensure_corpus(context, S1_SCENARIO_ID)
    async with context.app.import_epub(context.params.corpus) as book:
        target = await open_probe_chapter(context, book)
        check_available_count(
            "paragraphs",
            requested=target + 1,
            available=len(book.paragraphs),
        )


async def run_s2_continuous_reading_comments(context: ScenarioContext) -> None:
    ensure_corpus(context, S2_SCENARIO_ID)
    policy = policy_from_params(context)
    async with context.app.import_epub(context.params.corpus) as book:
        await open_probe_chapter(context, book)
        await read_comment_windows(context, book, policy, policy.min_comment_windows)


async def run_s3_fast_scroll(context: ScenarioContext) -> None:
    ensure_corpus(context, S3_SCENARIO_ID)
    policy = policy_from_params(context)
    async with context.app.import_epub(context.params.corpus) as book:
        await open_probe_chapter(context, book)
        positions = fast_scroll_positions(book, policy)
        if not positions:
            fail("fast scroll requires at least one reachable paragraph")

        await asyncio.gather(
            *[
                context.user.fast_scroll_to(
                    book,
                    paragraph_idx,
                    scroll_pct=min(1.0, 0.2 + offset * 0.2),
                )
                for offset, paragraph_idx in enumerate(positions)
            ]
        )

        await check_progress_at(book, positions[-1])
        window = await book.wait_for_current_window_ready(
            context.user,
            timeout_s=policy.max_wait_comment_s,
        )
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
                policy,  # type: ignore[arg-type]
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
                policy,  # type: ignore[arg-type]
            )
        response = await context.app.chat(
            book,
            paragraph_idx=min(target, max(0, book.progress_paragraph_idx)),
            message=policy.chat_questions[0],
        )
        await context.user.wait_for_chat_response(response)
        check_chat_response(response)
        check_chat_usage(response)


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


async def run_s6_followup_chat(context: ScenarioContext) -> None:
    ensure_corpus(context, S6_SCENARIO_ID)
    policy = policy_from_params(context, min_chat_turns=2)
    async with context.app.import_epub(context.params.corpus) as book:
        target = await prepare_chat_position(context, book)
        turns = s6_turn_count(policy)
        responses = await ask_chat_turns(context, book, policy, target, turns=turns)
        check_followup_session(responses[0], responses[1])
        check_chat_requests_without_selection(
            scenario_api_interactions(context, S6_SCENARIO_ID),
            minimum=len(responses),
        )
        await trigger_new_context_compaction(context, book, policy, target)


async def check_s2_agent_evidence(context: ScenarioContext) -> None:
    calls = context.llm.calls(scenario_id=S2_SCENARIO_ID)
    check_agent_coverage(calls, required_agents=("ParagraphCommentAgent",))


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
    check_compaction_summary_reused(calls)


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


async def check_s5_chat_evidence(context: ScenarioContext) -> None:
    check_chat_requests_without_selection(
        scenario_api_interactions(context, S5_SCENARIO_ID)
    )
    calls = context.llm.calls("ReadingChatAgent", scenario_id=S5_SCENARIO_ID)
    check_agent_coverage(calls, required_agents=("ReadingChatAgent",))
    check_chat_context_prompt(calls[-1], paragraph_idx=probe_paragraph(context))
    check_token_usage(
        calls[-1].usage,
        allowed_sources={"estimate", "provider", "framework"},
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
        check_chat_context_prompt(call, paragraph_idx=probe_paragraph(context))
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

    previous_answer = extract_chat_response_text(calls[-2].response)
    if previous_answer and previous_answer not in followup_prompt:
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
        *(
            text
            for text in (extract_chat_response_text(call.response) for call in calls)
            if text
        ),
    ]
    for call in later_compactions:
        for fragment in forbidden_fragments:
            if fragment and fragment in call.prompt:
                fail(
                    "chat history leaked into compaction prompt",
                    invocation_id=call.id,
                )


def ensure_corpus(context: ScenarioContext, scenario_id: str) -> None:
    if context.params.corpus is None:
        fail(f"{scenario_id} requires a resolved corpus")


def scenario_api_interactions(
    context: ScenarioContext,
    scenario_id: str,
) -> list[Any]:
    return [
        item
        for item in context.llm.hub.api_interactions
        if item.correlation.scenario_id == scenario_id
    ]


async def open_probe_chapter(context: ScenarioContext, book: BookFacade) -> int:
    probe = context.params.probe
    start_chapter = int(value_or_default(probe, "start_chapter_idx", 0))
    start_paragraph = int(value_or_default(probe, "start_paragraph_idx", 0))
    target_paragraph = int(value_or_default(probe, "paragraph_idx", start_paragraph))
    await context.user.open_chapter(book, start_chapter)
    if not book.paragraphs:
        fail("chapter has no paragraphs", chapter_idx=start_chapter)
    if start_paragraph > 0:
        await context.user.read_until(
            book,
            min(start_paragraph, len(book.paragraphs) - 1),
        )
    return min(target_paragraph, len(book.paragraphs) - 1)


def probe_paragraph(context: ScenarioContext) -> int:
    probe = context.params.probe
    return int(value_or_default(probe, "paragraph_idx", 0))


async def read_comment_windows(
    context: ScenarioContext,
    book: BookFacade,
    policy: ScenarioPolicy,
    minimum: int,
) -> None:
    observed = 0
    last_window: Any = None
    for _ in range(policy.read_batches):
        target = min(
            len(book.paragraphs) - 1,
            book.progress_paragraph_idx + policy.read_batch_size,
        )
        if target <= book.progress_paragraph_idx:
            break
        await context.user.read_until(book, target)
        window = await book.wait_for_current_window_ready(
            context.user,
            timeout_s=policy.max_wait_comment_s,
        )
        comments = await book.wait_for_comments(
            context.user,
            window.focus_start,
            window.focus_end,
            timeout_s=policy.max_wait_comment_s,
            required=True,
        )
        check_comments(
            comments,
            start=window.focus_start,
            end=window.focus_end,
            minimum=1,
        )
        if window.identity != last_window:
            observed += 1
            last_window = window.identity
        if observed >= minimum:
            return
    fail("not enough comment windows", expected=f">={minimum}", actual=observed)


async def prepare_chat_position(context: ScenarioContext, book: BookFacade) -> int:
    return await open_probe_chapter(context, book)


async def ask_chat_turns(
    context: ScenarioContext,
    book: BookFacade,
    policy: ScenarioPolicy,
    paragraph_idx: int,
    *,
    turns: int,
) -> list[ChatResponse]:
    questions = expanded_chat_questions(policy, turns)
    responses: list[ChatResponse] = []
    session_id: int | None = None
    for question in questions:
        response = await context.app.chat(
            book,
            paragraph_idx=paragraph_idx,
            message=question,
            session_id=session_id,
        )
        await context.user.wait_for_chat_response(response)
        check_chat_response(response)
        check_chat_usage(response)
        responses.append(response)
        session_id = response.session_id or session_id
    return responses


async def trigger_new_context_compaction(
    context: ScenarioContext,
    book: BookFacade,
    policy: ScenarioPolicy,
    target_paragraph: int,
) -> None:
    before_count = len(
        context.llm.calls("ContextCompactionAgent", scenario_id=S6_SCENARIO_ID)
    )
    async with context.app.subscribe_events(
        book_id=book.id,
        chapter_idx=book.chapter_idx,
    ) as events:
        cursor = events.cursor()
        max_paragraph = max(target_paragraph, len(book.paragraphs) - 1)
        for _ in range(policy.read_batches):
            target = min(
                max_paragraph,
                book.progress_paragraph_idx + policy.read_batch_size,
            )
            if target <= book.progress_paragraph_idx:
                break
            await context.user.read_until(book, target)
            window = await book.wait_for_current_window_ready(
                context.user,
                timeout_s=policy.max_wait_comment_s,
            )
            await book.wait_for_comments(
                context.user,
                window.focus_start,
                window.focus_end,
                timeout_s=policy.max_wait_comment_s,
                required=False,
            )
            if new_compaction_observed(context, book, before_count, events, cursor):
                return
        await context.user.wait_until(
            "post-chat context compaction",
            lambda: new_compaction_observed(
                context, book, before_count, events, cursor
            ),
            timeout_s=policy.max_wait_compaction_s,
            correlation=book.client.correlation,
        )


def new_compaction_observed(
    context: ScenarioContext,
    book: BookFacade,
    before_count: int,
    events: Any,
    cursor: int,
) -> bool:
    current_count = len(
        context.llm.calls("ContextCompactionAgent", scenario_id=S6_SCENARIO_ID)
    )
    if current_count > before_count:
        return True
    for event in events.events[cursor:]:
        if (
            event.correlation.book_id != book.id
            or event.correlation.chapter_idx != book.chapter_idx
        ):
            continue
        if event.event_type == "context.failed":
            raise RuntimeError(
                f"context compaction failed for book={book.id} "
                f"chapter={book.chapter_idx}: {event.data.get('error', '')}"
            )
        if event.event_type == "context.compacted":
            return True
    return False


async def check_progress_at(book: BookFacade, paragraph_idx: int) -> None:
    progress = await book.get_progress()
    actual = int(progress.get("paragraph_idx", -1))
    if actual != paragraph_idx:
        fail("latest progress was not persisted", expected=paragraph_idx, actual=actual)


def fast_scroll_positions(
    book: BookFacade,
    policy: ScenarioPolicy,
) -> tuple[int, ...]:
    max_idx = max(0, len(book.paragraphs) - 1)
    base = book.progress_paragraph_idx
    positions: list[int] = []
    for offset in policy.fast_scroll_offsets:
        value = min(max_idx, base + max(1, int(offset)))
        if value > base and (not positions or positions[-1] != value):
            positions.append(value)
    return tuple(positions)


def policy_from_params(
    context: ScenarioContext,
    *,
    min_chat_turns: int | None = None,
) -> ScenarioPolicy:
    values = context.params.values
    questions = values.get("chat_questions")
    if isinstance(questions, str):
        chat_questions = (questions,)
    elif questions:
        chat_questions = tuple(str(item) for item in questions)
    else:
        chat_questions = ScenarioPolicy.chat_questions
    offsets = tuple(
        int(item) for item in values.get("fast_scroll_offsets", (1, 2, 4, 6))
    )
    return ScenarioPolicy(
        read_batch_size=int(values.get("read_batch_size", 4)),
        read_batches=int(values.get("read_batches", 6)),
        min_comment_windows=int(values.get("min_comment_windows", 1)),
        post_compaction_comment_windows=int(
            values.get("post_compaction_comment_windows", 0)
        ),
        min_chat_turns=(
            int(values.get("min_chat_turns", 1))
            if min_chat_turns is None
            else min_chat_turns
        ),
        max_wait_comment_s=float(values.get("max_wait_comment_s", 60.0)),
        max_wait_compaction_s=float(values.get("max_wait_compaction_s", 120.0)),
        fast_scroll_offsets=offsets,
        chat_history_recent_turns=int(values.get("chat_history_recent_turns", 6)),
        s6_chat_turns=int(values.get("s6_chat_turns", 8)),
        chat_questions=chat_questions,
    )


def value_or_default(probe: Any, name: str, default: Any) -> Any:
    value = getattr(probe, name, None)
    return default if value is None else value


def s6_turn_count(policy: ScenarioPolicy) -> int:
    return max(3, policy.s6_chat_turns, policy.chat_history_recent_turns + 2)


def expanded_chat_questions(policy: ScenarioPolicy, turns: int) -> tuple[str, ...]:
    questions = list(policy.chat_questions)
    while len(questions) < turns:
        next_turn = len(questions) + 1
        questions.append(f"连续追问第 {next_turn} 轮：请只基于最近上下文回答。")
    return tuple(questions[:turns])


def check_chat_context_prompt(call: Any, *, paragraph_idx: int) -> None:
    prompt = call.prompt
    if "mode = chat" not in prompt:
        fail("chat prompt missing current-context mode", invocation_id=call.id)
    marker = f"current_reading_paragraph_idx = {paragraph_idx}"
    if marker not in prompt:
        fail(
            "chat prompt missing current reading paragraph",
            expected=marker,
            invocation_id=call.id,
        )


def extract_chat_response_text(value: Any) -> str:
    if isinstance(value, dict):
        if str(value.get("content", "")).strip():
            return str(value["content"])
        choices = value.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            if isinstance(message, dict) and str(message.get("content", "")).strip():
                return str(message["content"])
    if isinstance(value, str):
        return value
    return ""
