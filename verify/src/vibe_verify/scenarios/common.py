"""Shared policy and behavior helpers for built-in scenario scripts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from vibe_verify.assertions import (
    check_chat_response,
    check_chat_usage,
    check_comments,
    check_progress,
    check_window_covers,
    fail,
)
from vibe_verify.driver import BookFacade, ChatResponse, EventSubscriber
from vibe_verify.scenario import ScenarioContext

DEFAULT_CORPUS_PURPOSE = "happy_path_current"
CORE_SUITES = frozenset({"core", "a4"})

S0_SCENARIO_ID = "S0_environment_connectivity"
S1_SCENARIO_ID = "S1_import_book"
S2_SCENARIO_ID = "S2_continuous_reading_comments"
S3_SCENARIO_ID = "S3_fast_scroll"
S4_SCENARIO_ID = "S4_context_compaction"
S5_SCENARIO_ID = "S5_direct_chat"
S6_SCENARIO_ID = "S6_followup_chat"


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


class CompactionReadingPolicy(Protocol):
    read_batch_size: int
    read_batches: int
    post_compaction_comment_windows: int
    max_wait_comment_s: float
    max_wait_compaction_s: float


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


def last_chat_paragraph(context: ScenarioContext, scenario_id: str) -> int:
    for item in reversed(context.llm.hub.user_interactions):
        if item.action != "chat" or item.correlation.scenario_id != scenario_id:
            continue
        paragraph_idx = item.arguments.get("paragraph_idx")
        if not isinstance(paragraph_idx, int):
            fail(
                "chat paragraph_idx missing from user evidence",
                scenario_id=scenario_id,
                actual=paragraph_idx,
            )
        return paragraph_idx
    fail("chat user evidence missing", scenario_id=scenario_id)


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
        check_window_covers(window, paragraph_idx=book.progress_paragraph_idx)
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


async def read_until_context_compacted(
    context: ScenarioContext,
    book: BookFacade,
    policy: CompactionReadingPolicy,
    *,
    events: EventSubscriber,
    target_paragraph: int,
) -> int:
    comment_windows = 0
    last_seen_window_id: Any = None
    max_paragraph = max(target_paragraph, len(book.paragraphs) - 1)

    for _ in range(policy.read_batches):
        await read_next_batch(context, book, policy, max_paragraph=max_paragraph)

        window = await book.wait_for_current_window_ready(
            context.user,
            timeout_s=policy.max_wait_comment_s,
        )
        check_window_covers(window, paragraph_idx=book.progress_paragraph_idx)
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
            if window.identity != last_seen_window_id:
                comment_windows += 1
                last_seen_window_id = window.identity

        if context_compacted(context, book, events):
            return comment_windows

    await wait_for_context_compaction(context, book, policy, events)
    return comment_windows


async def read_next_batch(
    context: ScenarioContext,
    book: BookFacade,
    policy: CompactionReadingPolicy,
    *,
    max_paragraph: int,
) -> None:
    current = book.progress_paragraph_idx
    next_paragraph = min(max_paragraph, current + policy.read_batch_size)
    if next_paragraph <= current:
        next_paragraph = min(max_paragraph, current + 1)
    await context.user.read_until(book, next_paragraph)


async def read_post_compaction_windows(
    context: ScenarioContext,
    book: BookFacade,
    policy: CompactionReadingPolicy,
) -> None:
    for completed in range(policy.post_compaction_comment_windows):
        if not book.paragraphs:
            fail("post-compaction comment window expected but chapter is empty")
        target = min(len(book.paragraphs) - 1, book.progress_paragraph_idx + 1)
        if target <= book.progress_paragraph_idx:
            fail(
                "post-compaction comment window expected but "
                "no unread paragraph remains",
                expected=policy.post_compaction_comment_windows,
                actual=completed,
            )
        await context.user.read_until(book, target)
        window = await book.wait_for_current_window_ready(
            context.user,
            timeout_s=policy.max_wait_comment_s,
        )
        check_window_covers(window, paragraph_idx=book.progress_paragraph_idx)
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


async def wait_for_context_compaction(
    context: ScenarioContext,
    book: BookFacade,
    policy: CompactionReadingPolicy,
    events: EventSubscriber,
) -> None:
    await context.user.wait_until(
        "context compaction",
        lambda: context_compacted(context, book, events),
        timeout_s=policy.max_wait_compaction_s,
        correlation=book.client.correlation,
    )


def context_compacted(
    context: ScenarioContext,
    book: BookFacade,
    events: EventSubscriber | None = None,
    *,
    scenario_id: str | None = None,
) -> bool:
    active_scenario_id = scenario_id or book.client.correlation.scenario_id
    if active_scenario_id and context.llm.calls(
        "ContextCompactionAgent", scenario_id=active_scenario_id
    ):
        return True
    if events is None:
        return False
    for event in reversed(events.events):
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
    *,
    scenario_id: str,
) -> None:
    before_count = len(
        context.llm.calls("ContextCompactionAgent", scenario_id=scenario_id)
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
            check_window_covers(window, paragraph_idx=book.progress_paragraph_idx)
            await book.wait_for_comments(
                context.user,
                window.focus_start,
                window.focus_end,
                timeout_s=policy.max_wait_comment_s,
                required=False,
            )
            if new_compaction_observed(
                context,
                book,
                before_count,
                events,
                cursor,
                scenario_id=scenario_id,
            ):
                return
        await context.user.wait_until(
            "post-chat context compaction",
            lambda: new_compaction_observed(
                context,
                book,
                before_count,
                events,
                cursor,
                scenario_id=scenario_id,
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
    *,
    scenario_id: str,
) -> bool:
    current_count = len(
        context.llm.calls("ContextCompactionAgent", scenario_id=scenario_id)
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
    check_progress(
        progress,
        book_id=book.id,
        chapter_idx=book.chapter_idx,
        paragraph_idx=paragraph_idx,
    )


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
