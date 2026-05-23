"""SSE event collector for monitoring backend events during verification."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from .run import RunManager


class SSEEvent:
    """A single collected SSE event."""

    __slots__ = (
        "event_id",
        "event_type",
        "data",
        "verify_run_id",
        "trace_id",
        "book_id",
        "chapter_idx",
        "paragraph_idx",
        "window_id",
        "job_id",
        "created_at",
    )

    def __init__(self, event_type: str, data: dict[str, Any]):
        self.event_id = data.get("event_id", "")
        self.event_type = event_type
        self.data = data
        self.verify_run_id = data.get("verify_run_id", "")
        self.trace_id = data.get("trace_id", "")
        self.book_id = data.get("book_id")
        self.chapter_idx = data.get("chapter_idx")
        self.paragraph_idx = data.get("paragraph_idx")
        self.window_id = data.get("window_id")
        self.job_id = data.get("job_id")
        self.created_at = data.get(
            "created_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "data": self.data,
            "verify_run_id": self.verify_run_id,
            "trace_id": self.trace_id,
            "book_id": self.book_id,
            "chapter_idx": self.chapter_idx,
            "window_id": self.window_id,
            "created_at": self.created_at,
        }


class SSEEventCollector:
    """Collects SSE events from the backend event stream."""

    def __init__(
        self,
        base_url: str,
        run_manager: RunManager,
        verify_scenario_id: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.run_manager = run_manager
        self.verify_scenario_id = verify_scenario_id
        self._events: list[SSEEvent] = []
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def events(self) -> list[SSEEvent]:
        return list(self._events)

    def events_by_type(self, event_type: str) -> list[SSEEvent]:
        return [e for e in self._events if e.event_type == event_type]

    def events_for_book(self, book_id: int) -> list[SSEEvent]:
        return [e for e in self._events if e.book_id == book_id]

    def latest_event(self, event_type: str) -> SSEEvent | None:
        matching = self.events_by_type(event_type)
        return matching[-1] if matching else None

    async def start(self, params: dict | None = None) -> None:
        """Start collecting events in the background."""
        self._stop.clear()
        self._task = asyncio.create_task(self._collect_loop(params))

    async def stop(self) -> None:
        """Stop collecting events."""
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    async def wait_for_event(
        self,
        event_type: str | tuple[str, ...] | list[str],
        timeout_s: float = 60.0,
        predicate: Any = None,
    ) -> SSEEvent | None:
        """Wait until a matching event is collected, or timeout."""
        types = (event_type,) if isinstance(event_type, str) else tuple(event_type)
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            for evt in reversed(self._events):
                if evt.event_type in types:
                    if predicate is None or predicate(evt):
                        return evt
            await asyncio.sleep(0.2)
        return None

    async def _collect_loop(self, params: dict | None = None) -> None:
        url = f"{self.base_url}/api/events"
        headers: dict[str, str] = {
            "X-Verify-Run-Id": self.run_manager.run_id,
            "Accept": "text/event-stream",
        }
        if self.verify_scenario_id:
            headers["X-Verify-Scenario-Id"] = self.verify_scenario_id

        query_params = params or {}

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=10.0)
            ) as client:
                async with client.stream(
                    "GET", url, headers=headers, params=query_params
                ) as resp:
                    current_event_type = "message"
                    current_data_parts: list[str] = []

                    async for line in resp.aiter_lines():
                        if self._stop.is_set():
                            break

                        if line.startswith("event:"):
                            current_event_type = line[len("event:") :].strip()
                        elif line.startswith("data:"):
                            current_data_parts.append(line[len("data:") :].strip())
                        elif line == "" and current_data_parts:
                            data_str = "\n".join(current_data_parts)
                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                data = {"raw": data_str}

                            evt = SSEEvent(current_event_type, data)
                            self._events.append(evt)
                            self.run_manager.write_ndjson(
                                "sse_events.ndjson", [evt.to_dict()]
                            )
                            current_event_type = "message"
                            current_data_parts = []
        except Exception as exc:
            error_evt = SSEEvent("collector_error", {"error": str(exc)})
            self._events.append(error_evt)
