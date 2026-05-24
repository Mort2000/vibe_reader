from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


@router.get("/events")
async def event_stream(
    request: Request,
    book_id: int | None = Query(None),
    chapter_idx: int | None = Query(None),
) -> Any:
    from sse_starlette.sse import EventSourceResponse

    from ..infrastructure.events import SSEEventPublisher

    publisher: SSEEventPublisher = request.app.state.event_publisher
    queue = publisher.subscribe()

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
            publisher.unsubscribe(queue)

    return EventSourceResponse(generate())
