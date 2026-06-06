from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from ..application.agent_run_result import AgentRunResult, ChatAuditContext
from ..config import Settings
from ..observability import (
    mark_span_error,
    record_agent_metric,
    record_chat_metric,
    set_span_attributes,
    start_observable_span,
)
from ..repos import books as book_repo
from ..repos import chapters as chapter_repo
from ..repos import chat as chat_repo
from .agent_base import ChatDeps, get_chat_agent
from .context_builder import _estimate_text_tokens, build_context
from .token_estimator import TokenEstimator

logger = logging.getLogger(__name__)


def _build_message_history(
    turns: list[dict[str, Any]],
    max_turns: int,
    max_tokens: int,
) -> tuple[list[Any], list[dict[str, Any]], int]:
    if not turns:
        return [], [], 0

    completed = [t for t in turns if t.get("ai_msg")]
    recent = completed[:max_turns]
    recent.reverse()

    messages: list[Any] = []
    included_turns: list[dict[str, Any]] = []
    total_tokens = 0

    for t in recent:
        user_msg = t.get("user_msg", "")
        ai_msg = t.get("ai_msg") or ""
        turn_tokens = _estimate_text_tokens(user_msg) + _estimate_text_tokens(ai_msg)
        if total_tokens + turn_tokens > max_tokens:
            break
        messages.append(ModelRequest(parts=[UserPromptPart(content=user_msg)]))
        messages.append(ModelResponse(parts=[TextPart(content=ai_msg)]))
        included_turns.append({"user_msg": user_msg, "ai_msg": ai_msg})
        total_tokens += turn_tokens

    return messages, included_turns, total_tokens


@dataclass
class ChatContextResult:
    prompt: str
    context_hash: str
    estimated_tokens: int
    prompt_manifest: dict[str, Any] = field(default_factory=dict)
    message_history: list[Any] = field(default_factory=list)
    recent_chat_turns: list[dict[str, Any]] = field(default_factory=list)


async def build_chat_context(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    session_id: int,
    settings: Settings,
    token_estimator: TokenEstimator | None = None,
) -> ChatContextResult:
    book = await book_repo.get_book(db, book_id)
    chapter = await chapter_repo.get_chapter(db, book_id, chapter_idx)

    ctx_result = await build_context(
        db,
        book_id=book_id,
        chapter_idx=chapter_idx,
        reading_pidx=paragraph_idx,
        settings=settings,
        task_type="chat",
        book_title=book.get("title") if book else None,
        chapter_title=chapter.get("title") if chapter else None,
        token_estimator=token_estimator,
    )

    eph_cfg = settings.ephemeral_chat
    turns, _ = await chat_repo.list_turns(db, session_id, limit=100)
    message_history, recent_turns, chat_tokens = _build_message_history(
        turns, eph_cfg.recent_turns, eph_cfg.max_tokens
    )

    for comp in ctx_result.prompt_manifest.get("components", []):
        if comp.get("name") == "ephemeral_recent_chat":
            comp["tokens"] = chat_tokens
            comp["turn_count"] = len(recent_turns)
            break
    ctx_result.estimated_tokens += chat_tokens
    ctx_result.prompt_manifest["total_estimate"] = ctx_result.estimated_tokens
    for key in ("safe_total_estimate", "raw_total_estimate"):
        if ctx_result.prompt_manifest.get(key) is not None:
            ctx_result.prompt_manifest[key] = (
                int(ctx_result.prompt_manifest[key]) + chat_tokens
            )

    return ChatContextResult(
        prompt=ctx_result.prompt,
        context_hash=ctx_result.context_hash,
        estimated_tokens=ctx_result.estimated_tokens,
        prompt_manifest=ctx_result.prompt_manifest,
        message_history=message_history,
        recent_chat_turns=recent_turns,
    )


async def stream_llm_response(
    db: aiosqlite.Connection,
    *,
    book_id: int,
    chapter_idx: int,
    paragraph_idx: int,
    user_msg: str,
    prompt: str,
    message_history: list[Any] | None,
    session_id: int,
    turn_id: int,
    trace_id: str,
    chat_ctx: ChatContextResult,
    settings: Settings,
    job_runner: Any | None = None,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    agent = get_chat_agent(settings)
    deps = ChatDeps()
    t0 = time.monotonic()
    ttft_ms: float | None = None
    full_text = ""
    tokens_in = 0
    tokens_out = 0
    first_delta_emitted = False

    stream_result = None
    lock = job_runner.book_lock(book_id) if job_runner else nullcontext()
    async with lock:
        with start_observable_span(
            "ai.ReadingChatAgent.run",
            {
                "ai.agent": "ReadingChatAgent",
                "ai.model": settings.llm.model,
                "book.id": book_id,
                "chapter.idx": chapter_idx,
                "paragraph.idx": paragraph_idx,
                "chat.session_id": session_id,
                "chat.turn_id": turn_id,
                "app.trace_id": trace_id,
            },
        ) as span:
            try:
                async with agent.run_stream(
                    prompt, deps=deps, message_history=message_history,
                ) as stream:
                    stream_result = stream
                    async for chunk in stream.stream_text(delta=True):
                        full_text += chunk
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - t0) * 1000
                        if not first_delta_emitted:
                            first_delta_emitted = True
                            yield "chat.first_delta", {
                                "turn_id": turn_id,
                                "ttft_ms": round(ttft_ms, 1),
                            }
                        yield "chat.delta", {"turn_id": turn_id, "delta": chunk}

                    usage = stream.usage
                    tokens_in = usage.request_tokens or usage.input_tokens
                    tokens_out = usage.response_tokens or usage.output_tokens
            except Exception as exc:
                latency_ms = (time.monotonic() - t0) * 1000
                mark_span_error(span, exc)
                record_agent_metric(
                    agent="ReadingChatAgent",
                    model=settings.llm.model,
                    status="error",
                    duration_ms=latency_ms,
                )
                record_chat_metric(
                    status="error",
                    total_ms=latency_ms,
                    ttft_ms=ttft_ms,
                )
                raise

            latency_ms = (time.monotonic() - t0) * 1000
            set_span_attributes(
                span,
                {
                    "chat.ttft_ms": round(ttft_ms, 2) if ttft_ms is not None else None,
                    "duration_ms": round(latency_ms, 2),
                    "ai.input_tokens": tokens_in,
                    "ai.output_tokens": tokens_out,
                },
            )
            record_agent_metric(
                agent="ReadingChatAgent",
                model=settings.llm.model,
                status="ok",
                duration_ms=latency_ms,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
            )

    latency_ms = (time.monotonic() - t0) * 1000

    with start_observable_span(
        "service.chat.persist",
        {
            "book.id": book_id,
            "chapter.idx": chapter_idx,
            "paragraph.idx": paragraph_idx,
            "chat.session_id": session_id,
            "chat.turn_id": turn_id,
            "app.trace_id": trace_id,
        },
    ) as persist_span:
        try:
            await chat_repo.update_turn(
                db, turn_id,
                ai_msg=full_text, status="done",
                tokens_in=tokens_in, tokens_out=tokens_out,
                trace_id=trace_id,
            )
            await chat_repo.update_session_paragraph(db, session_id, paragraph_idx)
        except Exception as exc:
            mark_span_error(persist_span, exc, error_code="chat_persist_failed")
            record_chat_metric(
                status="error",
                total_ms=latency_ms,
                ttft_ms=ttft_ms,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
            )
            raise

    record_chat_metric(
        status="ok",
        total_ms=latency_ms,
        ttft_ms=ttft_ms,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
    )

    recorder = getattr(job_runner, "recorder", None) if job_runner else None
    if recorder:
        try:
            audit_ctx = ChatAuditContext(
                trace_id=trace_id,
                book_id=book_id,
                chapter_idx=chapter_idx,
                paragraph_idx=paragraph_idx,
                prompt=prompt,
                agent_result=stream_result,
                recent_chat_turns=chat_ctx.recent_chat_turns,
                user_msg=user_msg,
                prompt_manifest=chat_ctx.prompt_manifest,
            )
            await recorder.record(
                db,
                result=AgentRunResult(
                    agent_name="ReadingChatAgent",
                    duration_ms=round(latency_ms, 1),
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                    context_hash=chat_ctx.context_hash,
                    context_estimated_tokens=chat_ctx.estimated_tokens,
                    prompt_version="chat_v1",
                    prompt_manifest=chat_ctx.prompt_manifest,
                    audit_context=audit_ctx,
                ),
                settings=settings, trace_id=trace_id,
                job_id=0, book_id=book_id,
                chapter_idx=chapter_idx, window_id=None,
            )
        except Exception:
            logger.exception(
                "chat.recorder_failed",
                extra={
                    "event": "chat.recorder_failed",
                    "fields": {"turn_id": turn_id, "trace_id": trace_id},
                },
            )

    yield "chat.done", {
        "turn_id": turn_id, "ai_msg": full_text,
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
        "total_ms": round(latency_ms, 1),
    }
