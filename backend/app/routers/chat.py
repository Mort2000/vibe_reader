from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Path as PathParam, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatStreamRequest(BaseModel):
    book_id: int
    chapter_idx: int
    paragraph_idx: int = Field(..., ge=0)
    session_id: int | None = None
    user_msg: str = Field(..., min_length=1)


async def _resolve_session(db: Any, body: ChatStreamRequest) -> dict[str, Any]:
    from ..repos import chat as chat_repo

    if body.session_id is not None:
        session = await chat_repo.get_session_by_id(db, body.session_id)
        if session is not None and (
            session.get("book_id") != body.book_id
            or session.get("chapter_idx") != body.chapter_idx
        ):
            session = None
        if session is not None:
            return session
    return await chat_repo.get_or_create_session(
        db, book_id=body.book_id, chapter_idx=body.chapter_idx
    )


@router.post("/chat/stream")
async def chat_stream(request: Request, body: ChatStreamRequest) -> Any:
    from sse_starlette.sse import EventSourceResponse

    from ..observability import new_trace_id
    from ..repos import chat as chat_repo
    from ..services.chat_service import build_chat_context, stream_llm_response

    db = request.app.state.db
    settings = request.app.state.settings
    token_estimator = getattr(request.app.state, "token_estimator", None)
    job_runner = getattr(request.app.state, "job_runner", None)

    async def generate():
        trace_id = new_trace_id()
        session = await _resolve_session(db, body)
        session_id = session["id"]

        turn = await chat_repo.create_turn(
            db, session_id=session_id, book_id=body.book_id,
            chapter_idx=body.chapter_idx, paragraph_idx=body.paragraph_idx,
            user_msg=body.user_msg, status="streaming",
        )
        turn_id = turn["id"]

        yield {
            "event": "chat.started",
            "data": json.dumps(
                {"turn_id": turn_id, "session_id": session_id, "trace_id": trace_id},
                ensure_ascii=False,
            ),
        }

        try:
            chat_ctx = await build_chat_context(
                db, book_id=body.book_id, chapter_idx=body.chapter_idx,
                paragraph_idx=body.paragraph_idx, session_id=session_id,
                settings=settings, token_estimator=token_estimator,
            )
            prompt = chat_ctx.prompt + f"\n\n用户提问：{body.user_msg}"

            async for event_type, payload in stream_llm_response(
                db,
                book_id=body.book_id,
                chapter_idx=body.chapter_idx,
                paragraph_idx=body.paragraph_idx,
                user_msg=body.user_msg,
                prompt=prompt,
                message_history=chat_ctx.message_history or None,
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                chat_ctx=chat_ctx,
                settings=settings,
                job_runner=job_runner,
            ):
                if event_type == "chat.first_delta":
                    yield {
                        "event": "chat.first_delta",
                        "data": json.dumps(
                            {**payload, "session_id": session_id, "trace_id": trace_id},
                            ensure_ascii=False,
                        ),
                    }
                elif event_type == "chat.delta":
                    yield {
                        "event": "chat.delta",
                        "data": json.dumps(payload, ensure_ascii=False),
                    }
                elif event_type == "chat.done":
                    yield {
                        "event": "chat.done",
                        "data": json.dumps(
                            {**payload, "session_id": session_id, "trace_id": trace_id},
                            ensure_ascii=False,
                        ),
                    }
                    logger.info(
                        "chat.stream_done",
                        extra={
                            "event": "chat.stream_done",
                            "fields": {
                                "turn_id": turn_id,
                                "session_id": session_id,
                                "trace_id": trace_id,
                                "tokens_in": payload.get("tokens_in"),
                                "tokens_out": payload.get("tokens_out"),
                                "ttft_ms": payload.get("ttft_ms"),
                                "total_ms": payload.get("total_ms"),
                                "context_estimated_tokens": chat_ctx.estimated_tokens,
                            },
                        },
                    )
        except Exception as exc:
            logger.exception(
                "chat.stream_error",
                extra={
                    "event": "chat.stream_error",
                    "fields": {
                        "turn_id": turn_id,
                        "session_id": session_id,
                        "trace_id": trace_id,
                        "error": str(exc)[:200],
                    },
                },
            )
            await chat_repo.update_turn(
                db, turn_id, status="failed", trace_id=trace_id
            )
            yield {
                "event": "chat.error",
                "data": json.dumps(
                    {
                        "turn_id": turn_id,
                        "session_id": session_id,
                        "code": "stream_failed",
                        "message": str(exc)[:200],
                        "trace_id": trace_id,
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(generate())


@router.get("/books/{book_id}/chat/session")
async def get_chat_session(
    request: Request,
    book_id: int = PathParam(...),
    chapter_idx: int = Query(...),
) -> dict[str, Any]:
    from ..repos import chat as chat_repo

    db = request.app.state.db
    session = await chat_repo.get_or_create_session(
        db, book_id=book_id, chapter_idx=chapter_idx
    )
    return {"session": session}


@router.get("/chat/sessions/{session_id}/turns")
async def get_chat_turns(
    request: Request,
    session_id: int = PathParam(...),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    from ..repos import chat as chat_repo

    db = request.app.state.db
    turns, total = await chat_repo.list_turns(
        db, session_id, limit=limit, offset=offset
    )
    return {"items": turns, "total": total}
