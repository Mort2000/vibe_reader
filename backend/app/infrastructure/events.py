from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from ..observability import (
    get_request_id,
    get_trace_id,
    get_verify_run_id,
    get_verify_scenario_id,
    get_verify_step_id,
    record_sse_event_metric,
)

logger = logging.getLogger(__name__)


class EventPublisher(Protocol):
    async def publish(self, event: str, data: dict[str, Any]) -> None: ...


class SSEEventPublisher:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        trace_id = data.get("trace_id") or get_trace_id()
        request_id = data.get("request_id") or get_request_id()
        verify_run_id = data.get("verify_run_id") or get_verify_run_id()
        verify_scenario_id = (
            data.get("verify_scenario_id") or get_verify_scenario_id()
        )
        verify_step_id = data.get("verify_step_id") or get_verify_step_id()
        evt = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "event": event,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **data,
        }
        for key, value in {
            "trace_id": trace_id,
            "request_id": request_id,
            "verify_run_id": verify_run_id,
            "verify_scenario_id": verify_scenario_id,
            "verify_step_id": verify_step_id,
        }.items():
            if value and not evt.get(key):
                evt[key] = value

        delivered = 0
        dropped = 0
        for q in list(self._subscribers):
            try:
                q.put_nowait(evt)
                delivered += 1
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            record_sse_event_metric(event=event, status="dropped", count=dropped)
            logger.warning(
                "sse.queue_full",
                extra={
                    "event": "sse.queue_full",
                    "fields": {
                        "sse_event": event,
                        "subscriber_count": len(self._subscribers),
                        "dropped_count": dropped,
                    },
                },
            )
        if delivered:
            record_sse_event_metric(event=event, status="delivered")
        elif not dropped:
            record_sse_event_metric(event=event, status="no_subscribers")
