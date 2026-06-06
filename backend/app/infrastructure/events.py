from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol


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
        from ..observability import (
            get_request_id,
            get_trace_id,
            get_verify_run_id,
            get_verify_scenario_id,
            get_verify_step_id,
        )

        evt = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "event": event,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **data,
        }
        if not evt.get("request_id"):
            evt["request_id"] = get_request_id() or None
        if not evt.get("trace_id"):
            evt["trace_id"] = get_trace_id() or None
        if not evt.get("verify_run_id"):
            evt["verify_run_id"] = get_verify_run_id() or None
        if not evt.get("verify_scenario_id"):
            evt["verify_scenario_id"] = get_verify_scenario_id() or None
        if not evt.get("verify_step_id"):
            evt["verify_step_id"] = get_verify_step_id() or None
        for q in list(self._subscribers):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass
