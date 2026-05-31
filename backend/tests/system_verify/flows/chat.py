"""Direct chat and follow-up flows (V-10 / V-11 / R1 A4).

Backend prerequisites (P-12):
- ``POST /api/chat/stream`` must emit ``chat.started`` / ``chat.delta`` / ``chat.done``
- ``GET /api/verify/agent-runs`` must return ``ReadingChatAgent`` runs with
  ``injected_context`` for follow-up (S6) and post-compaction (A4) checks
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..assertions.chat import (
    assert_chat_request_has_no_selection,
    assert_chat_sse_contract,
    assert_chat_timing_observable,
    assert_chat_tokens_recorded,
    assert_followup_continuity,
    assert_recent_chat_in_injected_context,
    assert_stub_chat_context_markers,
)
from ..assertions.context import (
    assert_chapter_summary_in_subsequent_context,
    find_chat_agent_runs,
)
from ..core.client_factory import ChatStreamResult, TargetClient
from ..core.context import ScenarioContext
from ..core.scenario import StepAssertionError, assert_that

from .audit import fetch_verify_agent_runs
from .reading import ReadingCursor, advance_reading_to

logger = logging.getLogger(__name__)

DEFAULT_S5_QUESTIONS: tuple[str, ...] = (
    "这里为什么有点奇怪？",
    "这个人物现在是什么状态？",
    "刚才这几段在表达什么？",
)

DEFAULT_S6_FOLLOWUP = "你刚才说的压迫感是从哪里来的？"


@dataclass
class ChatTurnRecord:
    """One completed chat turn with audit metadata."""

    user_msg: str
    result: ChatStreamResult
    question_index: int = 0
    followup_of: int | None = None
    paragraph_idx: int = 0
    chapter_idx: int = 0
    source_paragraph_excerpt: str = ""
    neighbor_paragraphs: list[dict[str, Any]] = field(default_factory=list)
    recent_chat_tokens: int | None = None
    recent_chat_turns_clipped_count: int | None = None


async def advance_to_chat_position(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "advance_reading",
) -> None:
    """Move reading cursor to the scenario probe before chatting."""
    assert ctx.book_id is not None
    assert ctx.probe is not None
    cursor = ctx.cursor
    if not isinstance(cursor, ReadingCursor):
        cursor = ReadingCursor(ctx.probe.chapter_idx, ctx.probe.paragraph_idx)
        ctx.cursor = cursor

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        final_idx = await advance_reading_to(
            client,
            ctx,
            ctx.book_id,
            cursor.chapter_idx,
            ctx.probe.paragraph_idx,
            ctx.reading_trace,
            scenario_id=scenario_id,
            step_id=step_id,
            metrics=ctx.metrics,
        )
    cursor.paragraph_idx = final_idx
    ctx.chapter_idx = cursor.chapter_idx
    ctx.final_paragraph_idx = final_idx


async def send_direct_chat(
    ctx: ScenarioContext,
    client: TargetClient,
    user_msg: str,
    *,
    scenario_id: str,
    step_id: str,
    session_id: int | None = None,
    question_index: int = 0,
    followup_of: int | None = None,
) -> ChatTurnRecord:
    """Send one streaming chat turn at the current reading position."""
    assert ctx.book_id is not None
    cursor = ctx.cursor
    assert isinstance(cursor, ReadingCursor)

    body = {
        "book_id": ctx.book_id,
        "chapter_idx": cursor.chapter_idx,
        "paragraph_idx": cursor.paragraph_idx,
        "user_msg": user_msg,
    }
    if session_id is not None:
        body["session_id"] = session_id
    assert_chat_request_has_no_selection(body)

    result, rec = await client.stream_chat(
        ctx.book_id,
        cursor.chapter_idx,
        cursor.paragraph_idx,
        user_msg,
        session_id=session_id,
    )
    if rec.status_code == 404:
        raise StepAssertionError(
            assertion="chat_endpoint_available",
            message="POST /api/chat/stream returned 404 — chat API not available",
            actual={"status_code": rec.status_code},
        )
    if rec.status_code and rec.status_code >= 400:
        raise StepAssertionError(
            assertion="chat_http_status",
            message=f"Chat request failed with HTTP {rec.status_code}",
            actual={"status_code": rec.status_code, "error": rec.error},
        )

    turn = ChatTurnRecord(
        user_msg=user_msg,
        result=result,
        question_index=question_index,
        followup_of=followup_of,
        paragraph_idx=cursor.paragraph_idx,
        chapter_idx=cursor.chapter_idx,
    )
    ctx.chat_turns.append(turn)
    if result.session_id is not None:
        ctx.chat_session_id = result.session_id
    return turn


async def verify_s5_chat_turn(
    ctx: ScenarioContext,
    turn: ChatTurnRecord,
    *,
    scenario_id: str,
    step_id: str,
) -> None:
    """Validate one direct chat turn against S5 expectations."""
    config = ctx.config
    result = turn.result
    assert_chat_sse_contract(result)
    assert_chat_timing_observable(result)
    assert_chat_tokens_recorded(result, config)

    if config.llm.mode == "stub":
        assert_stub_chat_context_markers(
            result,
            chapter_idx=turn.chapter_idx,
            paragraph_idx=turn.paragraph_idx,
            stub_profile=config.llm.stub_profile,
        )

    ctx.metrics.record(
        "chat.ttft_ms",
        result.ttft_ms or 0,
        unit="ms",
        scenario_id=scenario_id,
        step_id=step_id,
    )
    ctx.metrics.record(
        "chat.total_ms",
        result.total_ms or 0,
        unit="ms",
        scenario_id=scenario_id,
        step_id=step_id,
    )
    if result.tokens_in is not None:
        ctx.metrics.record(
            "chat.tokens.input",
            result.tokens_in,
            unit="tokens",
            scenario_id=scenario_id,
            step_id=step_id,
        )
    if result.tokens_out is not None:
        ctx.metrics.record(
            "chat.tokens.output",
            result.tokens_out,
            unit="tokens",
            scenario_id=scenario_id,
            step_id=step_id,
        )


async def run_s5_direct_chat(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "direct_chat",
    user_msg: str | None = None,
) -> ChatTurnRecord:
    """Send the primary S5 chat question and validate the response."""
    question = user_msg or DEFAULT_S5_QUESTIONS[0]
    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        turn = await send_direct_chat(
            ctx,
            client,
            question,
            scenario_id=scenario_id,
            step_id=step_id,
            session_id=ctx.chat_session_id,
            question_index=0,
        )
    await verify_s5_chat_turn(ctx, turn, scenario_id=scenario_id, step_id=step_id)
    return turn


async def run_s6_followup_chat(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "followup_chat",
    followup_msg: str | None = None,
) -> ChatTurnRecord:
    """Send a follow-up chat turn referencing the prior answer."""
    if not ctx.chat_turns:
        raise StepAssertionError(
            assertion="s6_requires_prior_turn",
            message="S6 follow-up requires a prior chat turn in context",
        )
    first = ctx.chat_turns[0]
    question = followup_msg or DEFAULT_S6_FOLLOWUP
    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        turn = await send_direct_chat(
            ctx,
            client,
            question,
            scenario_id=scenario_id,
            step_id=step_id,
            session_id=first.result.session_id or ctx.chat_session_id,
            question_index=1,
            followup_of=0,
        )
    await verify_s5_chat_turn(ctx, turn, scenario_id=scenario_id, step_id=step_id)
    assert_followup_continuity(first.result, turn.result, followup_user_msg=question)
    return turn


async def verify_s6_recent_chat_context(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "verify_followup_context",
) -> None:
    """Confirm follow-up chat agent context includes recent chat history."""
    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        agent_runs = await fetch_verify_agent_runs(
            client,
            ctx.run_manager.run_id,
            scenario_id=scenario_id,
            step_id=step_id,
        )
    chat_runs = find_chat_agent_runs(agent_runs)
    assert_that.gte(len(chat_runs), 1, label="chat_agent_runs_recorded")
    ctx.chat_agent_runs = chat_runs

    latest = chat_runs[-1]
    injected = (latest.get("interaction") or latest).get("injected_context") or {}
    assert_recent_chat_in_injected_context(injected, min_turns=1)

    recent_tokens = injected.get("recent_chat_tokens")
    if recent_tokens is not None:
        ctx.metrics.record(
            "recent_chat_turns_tokens",
            float(recent_tokens),
            unit="tokens",
            scenario_id=scenario_id,
            step_id=step_id,
        )
    clipped = injected.get("recent_chat_turns_clipped_count")
    if clipped is not None:
        ctx.metrics.record(
            "recent_chat_turns_clipped_count",
            float(clipped),
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
        )
        if ctx.chat_turns:
            ctx.chat_turns[-1].recent_chat_turns_clipped_count = int(clipped)
    if recent_tokens is not None and ctx.chat_turns:
        ctx.chat_turns[-1].recent_chat_tokens = int(recent_tokens)


async def post_compaction_chat_a4(
    ctx: ScenarioContext,
    *,
    scenario_id: str,
    step_id: str = "post_compaction_chat",
    user_msg: str | None = None,
) -> ChatTurnRecord:
    """Send chat after compaction and verify chapter summary is in context."""
    cursor = ctx.cursor
    assert isinstance(cursor, ReadingCursor)
    question = user_msg or "压缩之后，前面章节的主要情节是什么？"

    async with TargetClient(
        ctx.config.target.base_url,
        ctx.run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        turn = await send_direct_chat(
            ctx,
            client,
            question,
            scenario_id=scenario_id,
            step_id=step_id,
            session_id=ctx.chat_session_id,
        )
        await verify_s5_chat_turn(ctx, turn, scenario_id=scenario_id, step_id=step_id)

        agent_runs = await fetch_verify_agent_runs(
            client,
            ctx.run_manager.run_id,
            scenario_id=scenario_id,
            step_id=step_id,
        )

    chat_runs = find_chat_agent_runs(agent_runs)
    assert_that.gte(len(chat_runs), 1, label="post_compaction_chat_agent_runs")
    ctx.chat_agent_runs = chat_runs

    compaction_runs = ctx.compaction_agent_runs or []
    assert_that.gte(
        len(compaction_runs),
        1,
        label="compaction_agent_runs_before_post_compaction_chat",
    )
    latest_chat = chat_runs[-1]
    injected = (latest_chat.get("interaction") or latest_chat).get("injected_context") or {}
    assert_chapter_summary_in_subsequent_context(
        injected,
        compaction_run=compaction_runs[-1],
    )

    ctx.run_manager.real_llm_tracker.phase_coverage["A4_full_flow"] = True
    return turn
