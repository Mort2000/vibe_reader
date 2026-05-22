from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

_event_subscribers: list[asyncio.Queue[dict[str, Any]]] = []


def _event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def publish_event(event: str, data: dict[str, Any]) -> None:
    evt = {
        "event_id": _event_id(),
        "event": event,
        "created_at": _now_iso(),
        **data,
    }
    for q in _event_subscribers:
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            pass


@router.get("/events")
async def event_stream(
    request: Request,
    book_id: int | None = Query(None),
    chapter_idx: int | None = Query(None),
) -> Any:
    from sse_starlette.sse import EventSourceResponse

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
    _event_subscribers.append(queue)

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue

                if book_id is not None and evt.get("book_id") != book_id:
                    continue
                if chapter_idx is not None and evt.get("chapter_idx") != chapter_idx:
                    continue

                yield {
                    "id": evt.get("event_id", ""),
                    "event": evt.get("event", ""),
                    "data": json.dumps(evt, ensure_ascii=False),
                }
        finally:
            _event_subscribers.remove(queue)

    return EventSourceResponse(generate())
