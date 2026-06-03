"""R1 A4 full-flow user script for post-compaction streaming chat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vibe_verify.assertions import (
    check_agent_coverage,
    check_available_count,
    check_chat_response,
    check_compaction_summary_reused,
    fail,
)
from vibe_verify.driver import BookFacade
from vibe_verify.scenario import ScenarioContext, ScenarioDefinition
from vibe_verify.scenarios.common import (
    read_post_compaction_windows,
    read_until_context_compacted,
    value_or_default,
)

R1_A4_SCENARIO_ID = "R1_A4_full_flow"


@dataclass(frozen=True)
class R1A4Policy:
    read_batch_size: int = 64
    read_batches: int = 8
    min_comment_windows: int = 2
    post_compaction_comment_windows: int = 1
    min_chat_turns: int = 1
    max_wait_comment_s: float = 30.0
    max_wait_compaction_s: float = 60.0
    chat_questions: tuple[str, ...] = (
        "压缩后的上下文里，当前情节最关键的压力是什么？",
        "你刚才提到的压力具体来自哪里？",
    )


def r1_a4_full_flow() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=R1_A4_SCENARIO_ID,
        script=run_r1_a4_full_flow,
        suites=frozenset({"real-happy-path", "r1-a4"}),
        profiles=frozenset({"r1_a4_stub", "r1_a4_real"}),
        corpus_purpose="happy_path_current",
        description=(
            "R1 A4 full flow: import, continuous reading, comments, "
            "compaction, and post-compaction streaming chat"
        ),
        post_checks=(check_r1_agent_evidence,),
    )


async def run_r1_a4_full_flow(context: ScenarioContext) -> None:
    policy = policy_from_params(context)
    validate_policy(policy)
    if context.params.corpus is None:
        fail("R1 A4 requires a resolved corpus")

    # 步骤 1：根据语料 probe 确定起读章节、起读段落和后续提问锚点。
    probe = context.params.probe
    start_chapter = int(
        value_or_default(
            probe, "start_chapter_idx", probe_value(probe, "chapter_idx", 0)
        )
    )
    start_paragraph = int(value_or_default(probe, "start_paragraph_idx", 0))
    target_paragraph = int(probe_value(probe, "paragraph_idx", start_paragraph))

    async with context.app.import_epub(context.params.corpus) as book:
        # 步骤 2：模拟用户导入图书后打开章节，并跳到 probe 指定的阅读起点。
        await context.user.open_chapter(book, start_chapter)
        if start_paragraph > 0:
            await context.user.read_until(book, start_paragraph)

        async with context.app.subscribe_events(
            book_id=book.id,
            chapter_idx=book.chapter_idx,
        ) as events:
            # 步骤 3：持续阅读并检查评论窗口，直到上下文压缩可观测。
            comment_windows = await read_until_context_compacted(
                context,
                book,
                policy,
                events=events,
                target_paragraph=target_paragraph,
            )
        if comment_windows < policy.min_comment_windows:
            fail(
                "not enough comment windows before compaction",
                expected=policy.min_comment_windows,
                actual=comment_windows,
            )

        # 步骤 4：压缩后继续阅读，确认评论链路仍能正常工作。
        await read_post_compaction_windows(context, book, policy)

        # 步骤 5：围绕当前窗口发起聊天，验证压缩上下文能被后续问答使用。
        await ask_post_compaction_questions(context, book, policy, target_paragraph)


async def check_r1_agent_evidence(context: ScenarioContext) -> None:
    # 事后校验：用户路径完成后，再检查 Agent 覆盖与压缩摘要复用证据。
    calls = context.llm.calls(scenario_id=R1_A4_SCENARIO_ID)
    check_agent_coverage(
        calls,
        required_agents=(
            "ParagraphCommentAgent",
            "ContextCompactionAgent",
            "ReadingChatAgent",
        ),
    )
    check_compaction_summary_reused(calls)


async def ask_post_compaction_questions(
    context: ScenarioContext,
    book: BookFacade,
    policy: R1A4Policy,
    target_paragraph: int,
) -> None:
    session_id: int | None = None
    for question in policy.chat_questions[: policy.min_chat_turns]:
        # 多轮问题复用同一 session，验证后续追问仍保留阅读上下文。
        response = await context.app.chat(
            book,
            paragraph_idx=min(target_paragraph, max(0, book.progress_paragraph_idx)),
            message=question,
            session_id=session_id,
        )
        await context.user.wait_for_chat_response(response)
        check_chat_response(response)
        session_id = response.session_id or session_id


def policy_from_params(context: ScenarioContext) -> R1A4Policy:
    values = context.params.values
    questions = values.get("chat_questions")
    if isinstance(questions, str):
        chat_questions = (questions,)
    elif questions:
        chat_questions = tuple(str(item) for item in questions)
    else:
        chat_questions = R1A4Policy.chat_questions
    return R1A4Policy(
        read_batch_size=int(values.get("read_batch_size", 64)),
        read_batches=int(values.get("read_batches", 8)),
        min_comment_windows=int(values.get("min_comment_windows", 2)),
        post_compaction_comment_windows=int(
            values.get("post_compaction_comment_windows", 1)
        ),
        min_chat_turns=int(values.get("min_chat_turns", 1)),
        max_wait_comment_s=float(values.get("max_wait_comment_s", 30.0)),
        max_wait_compaction_s=float(values.get("max_wait_compaction_s", 60.0)),
        chat_questions=chat_questions,
    )


def validate_policy(policy: R1A4Policy) -> None:
    if policy.min_chat_turns < 1:
        fail("min_chat_turns must be positive", actual=policy.min_chat_turns)
    check_available_count(
        "chat questions",
        requested=policy.min_chat_turns,
        available=len(policy.chat_questions),
    )


def probe_value(probe: Any, name: str, default: Any) -> Any:
    return getattr(probe, name, default) if probe is not None else default
