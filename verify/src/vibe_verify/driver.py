"""Formal HTTP / SSE product driver and user-facing facades."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

import httpx

from .artifact_store import redact_headers
from .evidence import EvidenceHub
from .models import (
    APIInteraction,
    Correlation,
    SSEEvent,
    UserInteraction,
    merge_correlation,
    optional_int,
)


class Clock(Protocol):
    async def reading(self, paragraphs: int) -> None: ...

    async def paging(self) -> None: ...

    async def waiting(self, seconds: float) -> None: ...

    async def polling(self) -> None: ...

    def patience_s(self) -> float: ...


@dataclass(frozen=True)
class APIResponse:
    status_code: int
    body: Any
    headers: dict[str, str]
    correlation: Correlation


@dataclass
class ChatResponse:
    text: str = ""
    events: list[SSEEvent] = field(default_factory=list)
    session_id: int | None = None
    turn_id: int | None = None
    ttft_ms: float | None = None
    duration_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReadingWindow:
    start: int
    end: int
    raw: dict[str, Any]
    comments_ready_count: int = 0
    comments_target_count: int = 0

    @property
    def status(self) -> str:
        return str(self.raw.get("status") or "")

    @property
    def focus_start(self) -> int:
        return int(self.raw.get("focus_start_paragraph_idx", self.start))

    @property
    def focus_end(self) -> int:
        return int(self.raw.get("focus_end_paragraph_idx", self.end))

    @property
    def identity(self) -> int | tuple[int, int, int, int]:
        value = self.raw.get("id", self.raw.get("window_id"))
        return int(value) if value is not None else (
            self.start,
            self.end,
            self.focus_start,
            self.focus_end,
        )

    @property
    def is_ready(self) -> bool:
        return self.status == "done" or self.comments_ready_count > 0

    @property
    def has_failed(self) -> bool:
        return self.status == "failed"


class TargetClient:
    """Async client that records sanitized formal backend interactions."""

    def __init__(
        self,
        base_url: str,
        *,
        evidence: EvidenceHub,
        correlation: Correlation,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.evidence = evidence
        self.correlation = correlation
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=10),
        )

    def scoped(self, *, scenario_id: str = "", step_id: str = "") -> TargetClient:
        self.correlation = replace(
            self.correlation,
            scenario_id=scenario_id or self.correlation.scenario_id,
            step_id=step_id or self.correlation.step_id,
        )
        return self

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> APIResponse:
        request_headers = self._headers(headers)
        request_started = time.monotonic()
        correlation = self.correlation
        try:
            response = await self._client.request(
                method,
                path,
                json=json_body,
                params=params,
                files=files,
                headers=request_headers,
            )
            duration_ms = (time.monotonic() - request_started) * 1000
            body = parse_response_body(response)
            correlation = response_correlation(self.correlation, response.headers)
            self.evidence.record_api(
                APIInteraction(
                    method=method.upper(),
                    path=path,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                    correlation=correlation,
                    request_headers=request_headers,
                    request_body={"json": json_body, "params": params},
                    response_body=body,
                )
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - request_started) * 1000
            self.evidence.record_api(
                APIInteraction(
                    method=method.upper(),
                    path=path,
                    status_code=0,
                    duration_ms=duration_ms,
                    correlation=correlation,
                    request_headers=request_headers,
                    request_body={"json": json_body, "params": params},
                    error=str(exc),
                )
            )
            raise
        return APIResponse(
            status_code=response.status_code,
            body=body,
            headers=dict(response.headers),
            correlation=correlation,
        )

    async def stream_chat(self, payload: dict[str, Any]) -> ChatResponse:
        request_headers = self._headers({"Accept": "text/event-stream"})
        started = time.monotonic()
        first_delta_at: float | None = None
        result = ChatResponse()
        status_code = 0
        correlation = self.correlation
        response_body: Any = None
        error = ""
        try:
            async with self._client.stream(
                "POST",
                "/api/chat/stream",
                json=payload,
                headers=request_headers,
            ) as response:
                status_code = response.status_code
                correlation = response_correlation(self.correlation, response.headers)
                if response.status_code >= 400:
                    raw = await response.aread()
                    result.error = {
                        "status_code": response.status_code,
                        "body": raw.decode(errors="replace")[:500],
                    }
                    response_body = result.error
                    return result
                async for event_type, data in iter_sse(response.aiter_lines()):
                    event = SSEEvent(event_type, data, correlation)
                    result.events.append(event)
                    self.evidence.record_sse(event)
                    if event_type == "chat.delta":
                        if first_delta_at is None:
                            first_delta_at = time.monotonic()
                        result.text += str(data.get("delta", ""))
                    elif event_type == "chat.started":
                        result.session_id = optional_int(data.get("session_id"))
                        result.turn_id = optional_int(data.get("turn_id"))
                    elif event_type == "chat.done":
                        result.text = str(data.get("ai_msg") or result.text)
                        result.session_id = optional_int(data.get("session_id"))
                        result.turn_id = optional_int(data.get("turn_id"))
                        result.tokens_in = optional_int(data.get("tokens_in"))
                        result.tokens_out = optional_int(data.get("tokens_out"))
                    elif event_type == "chat.error":
                        result.error = data
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            result.duration_ms = (time.monotonic() - started) * 1000
            result.ttft_ms = (
                (first_delta_at - started) * 1000
                if first_delta_at is not None
                else None
            )
            if response_body is None:
                response_body = {
                    "event_count": len(result.events),
                    "ttft_ms": result.ttft_ms,
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "error": result.error,
                }
            self.evidence.record_api(
                APIInteraction(
                    method="POST",
                    path="/api/chat/stream",
                    status_code=status_code,
                    duration_ms=result.duration_ms,
                    correlation=correlation,
                    request_headers=redact_headers(request_headers),
                    request_body=summarize_body(payload),
                    response_body=response_body,
                    error=error,
                )
            )
        return result

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        headers["X-Verify-Run-Id"] = self.correlation.run_id
        if self.correlation.scenario_id:
            headers["X-Verify-Scenario-Id"] = self.correlation.scenario_id
        if self.correlation.step_id:
            headers["X-Verify-Step-Id"] = self.correlation.step_id
        return headers


class EventSubscriber:
    """Collect backend SSE events and wait for observable outcomes."""

    def __init__(self, evidence: EvidenceHub, correlation: Correlation):
        self.evidence = evidence
        self.correlation = correlation
        self.events: list[SSEEvent] = []

    def ingest(self, event_type: str, data: dict[str, Any]) -> SSEEvent:
        event = SSEEvent(
            event_type, data, correlation_from_data(self.correlation, data)
        )
        self.events.append(event)
        self.evidence.record_sse(event)
        return event

    async def collect(self, lines: AsyncIterator[str]) -> None:
        async for event_type, data in iter_sse(lines):
            self.ingest(event_type, data)

    def cursor(self) -> int:
        return len(self.events)

    async def subscribe(
        self,
        base_url: str,
        *,
        params: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 300,
        ready: asyncio.Future[None] | None = None,
    ) -> None:
        """Subscribe to the formal backend event stream until the stream ends."""
        headers = {
            "Accept": "text/event-stream",
            "X-Verify-Run-Id": self.correlation.run_id,
        }
        if self.correlation.scenario_id:
            headers["X-Verify-Scenario-Id"] = self.correlation.scenario_id
        if self.correlation.step_id:
            headers["X-Verify-Step-Id"] = self.correlation.step_id
        owns_client = client is None
        active = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_s, connect=10),
        )
        started = time.monotonic()
        status_code = 0
        error = ""
        try:
            async with active.stream(
                "GET", "/api/events", headers=headers, params=params
            ) as response:
                status_code = response.status_code
                if response.status_code >= 400:
                    raw = await response.aread()
                    error = (
                        f"/api/events HTTP {response.status_code}: "
                        f"{raw.decode(errors='replace')[:500]}"
                    )
                    if ready is not None and not ready.done():
                        ready.set_exception(RuntimeError(error))
                    raise RuntimeError(error)
                if ready is not None and not ready.done():
                    ready.set_result(None)
                async for event_type, data in iter_sse(response.aiter_lines()):
                    self.ingest(event_type, data)
        except Exception as exc:
            error = str(exc)
            if ready is not None and not ready.done():
                ready.set_exception(exc)
            raise
        finally:
            self.evidence.record_api(
                APIInteraction(
                    method="GET",
                    path="/api/events",
                    status_code=status_code,
                    duration_ms=(time.monotonic() - started) * 1000,
                    correlation=self.correlation,
                    request_headers=headers,
                    request_body={"params": params},
                    error=error,
                )
            )
            if owns_client:
                await active.aclose()

    async def wait_for(
        self,
        event_type: str,
        *,
        timeout_s: float,
        predicate: Any = None,
        after_index: int | None = None,
    ) -> SSEEvent:
        deadline = time.monotonic() + timeout_s
        start = after_index or 0
        while time.monotonic() < deadline:
            for event in reversed(self.events[start:]):
                if event.event_type == event_type and (
                    predicate is None or predicate(event)
                ):
                    return event
            await asyncio.sleep(0.01)
        raise TimeoutError(f"SSE event not observed: {event_type}")


class AppFacade:
    """Application-level actions exposed to scenario scripts."""

    def __init__(self, client: TargetClient, *, clock: Clock, evidence: EvidenceHub):
        self.client = client
        self.clock = clock
        self.evidence = evidence

    @asynccontextmanager
    async def import_epub(self, corpus: str | Path) -> AsyncIterator[BookFacade]:
        path = Path(corpus)
        started = time.monotonic()
        response = await self.client.request(
            "POST",
            "/api/books/import",
            files={"file": (path.name, path.read_bytes(), "application/epub+zip")},
        )
        require_success(response)
        body = unwrap(response.body)
        book_data = parse_imported_book(body)
        self._record_user("import_epub", {"path": str(path)}, started, body)
        yield BookFacade(self.client, self.clock, self.evidence, book_data)

    @asynccontextmanager
    async def subscribe_events(
        self,
        *,
        book_id: int | None = None,
        chapter_idx: int | None = None,
    ) -> AsyncIterator[EventSubscriber]:
        params = {
            key: value
            for key, value in {
                "book_id": book_id,
                "chapter_idx": chapter_idx,
            }.items()
            if value is not None
        }
        subscriber = EventSubscriber(self.evidence, self.client.correlation)
        started = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(
            subscriber.subscribe(
                self.client.base_url,
                params=params or None,
                client=self.client._client,
                ready=started,
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(started), timeout=5.0)
            yield subscriber
        finally:
            if task.done():
                with suppress(asyncio.CancelledError):
                    task.exception()
            else:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def chat(
        self,
        book: BookFacade,
        *,
        paragraph_idx: int,
        message: str,
        session_id: int | None = None,
    ) -> ChatResponse:
        started = time.monotonic()
        payload = {
            "book_id": book.id,
            "chapter_idx": book.chapter_idx,
            "paragraph_idx": paragraph_idx,
            "user_msg": message,
        }
        if session_id is not None:
            payload["session_id"] = session_id
        response = await self.client.stream_chat(payload)
        self._record_user(
            "chat",
            {
                "book_id": book.id,
                "paragraph_idx": paragraph_idx,
                "message": message,
                "session_id": session_id,
            },
            started,
            {"text": response.text, "error": response.error},
        )
        return response

    def _record_user(
        self, action: str, arguments: dict[str, Any], started: float, outcome: Any
    ) -> None:
        self.evidence.record_user(
            UserInteraction(
                action=action,
                arguments=arguments,
                correlation=self.client.correlation,
                duration_ms=(time.monotonic() - started) * 1000,
                outcome=summarize_body(outcome),
            )
        )


class BookFacade:
    """Scenario-facing book, chapter, progress, window, and comment view."""

    def __init__(
        self,
        client: TargetClient,
        clock: Clock,
        evidence: EvidenceHub,
        raw: dict[str, Any],
    ):
        self.client = client
        self.clock = clock
        self.evidence = evidence
        self.raw = raw
        self.id = int(raw["id"])
        self.chapter_idx = 0
        self.paragraphs: list[dict[str, Any]] = []
        self.progress_paragraph_idx = 0

    async def show_chapter(self, chapter_idx: int) -> list[dict[str, Any]]:
        response = await self.client.request(
            "GET", f"/api/books/{self.id}/chapters/{chapter_idx}/paragraphs"
        )
        require_success(response)
        self.chapter_idx = chapter_idx
        body = unwrap(response.body)
        self.paragraphs = parse_items(body, preferred_key="paragraphs")
        return self.paragraphs

    async def update_progress(
        self, paragraph_idx: int, *, scroll_pct: float = 0.0
    ) -> APIResponse:
        response = await self.client.request(
            "PUT",
            f"/api/books/{self.id}/progress",
            json_body={
                "chapter_idx": self.chapter_idx,
                "paragraph_idx": paragraph_idx,
                "scroll_pct": scroll_pct,
            },
        )
        require_success(response)
        self.progress_paragraph_idx = paragraph_idx
        return response

    async def get_current_window(self) -> ReadingWindow:
        response = await self.client.request(
            "GET",
            f"/api/books/{self.id}/chapters/{self.chapter_idx}/windows/current",
        )
        require_success(response)
        body = parse_current_window_response(unwrap(response.body))
        envelope = unwrap(response.body)
        return ReadingWindow(
            start=int(body["start_paragraph_idx"]),
            end=int(body["end_paragraph_idx"]),
            raw=body,
            comments_ready_count=int(
                envelope.get("comments_ready_count", 0)
                if isinstance(envelope, dict)
                else 0
            ),
            comments_target_count=int(
                envelope.get("comments_target_count", 0)
                if isinstance(envelope, dict)
                else 0
            ),
        )

    async def get_comments(self, start: int, end: int) -> list[dict[str, Any]]:
        response = await self.client.request(
            "GET",
            f"/api/books/{self.id}/chapters/{self.chapter_idx}/comments",
            params={"start": start, "end": end},
        )
        require_success(response)
        body = unwrap(response.body)
        return parse_items(body, preferred_key="comments")

    async def wait_for_current_window_ready(
        self,
        user: UserFacade,
        *,
        timeout_s: float | None = None,
    ) -> ReadingWindow:
        async def current_ready_window() -> ReadingWindow | None:
            window = await self.get_current_window()
            if window.has_failed:
                raise RuntimeError(
                    f"reading window failed: {window.identity}"
                )
            return window if window.is_ready else None

        return await user.wait_until(
            "current reading window ready",
            current_ready_window,
            timeout_s=timeout_s,
            correlation=self.client.correlation,
        )

    async def wait_for_comments(
        self,
        user: UserFacade,
        start: int,
        end: int,
        *,
        minimum: int = 1,
        timeout_s: float | None = None,
        required: bool = True,
    ) -> list[dict[str, Any]]:
        result = await user.wait_until(
            "comments ready",
            lambda: self.get_comments(start, end),
            accept=lambda comments: len(comments) >= minimum,
            timeout_s=timeout_s,
            required=required,
            correlation=self.client.correlation,
        )
        return result if len(result or []) >= minimum else []

    async def retry_window(self, window_id: int) -> APIResponse:
        started = time.monotonic()
        response = await self.client.request(
            "POST", f"/api/windows/{window_id}/retry"
        )
        require_success(response)
        self.evidence.record_user(
            UserInteraction(
                action="retry_window",
                arguments={"book_id": self.id, "window_id": window_id},
                correlation=response.correlation,
                duration_ms=(time.monotonic() - started) * 1000,
                outcome=summarize_body(unwrap(response.body)),
            )
        )
        return response

    def get_proceeded_paragraph_num(self) -> int:
        return self.progress_paragraph_idx + 1


class UserFacade:
    """User actions with profile-defined pacing."""

    def __init__(self, *, clock: Clock, evidence: EvidenceHub):
        self.clock = clock
        self.evidence = evidence

    async def open_chapter(self, book: BookFacade, chapter_idx: int) -> None:
        started = time.monotonic()
        await book.show_chapter(chapter_idx)
        self.evidence.record_user(
            UserInteraction(
                action="open_chapter",
                arguments={"book_id": book.id, "chapter_idx": chapter_idx},
                correlation=book.client.correlation,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        )

    async def read_until(self, book: BookFacade, paragraph_idx: int) -> None:
        distance = max(0, paragraph_idx - book.progress_paragraph_idx)
        await self.clock.reading(distance)
        await book.update_progress(paragraph_idx, scroll_pct=1.0)
        self.evidence.record_user(
            UserInteraction(
                action="read_until",
                arguments={"book_id": book.id, "paragraph_idx": paragraph_idx},
                correlation=book.client.correlation,
            )
        )

    async def page_down_or_next_chapter(self, book: BookFacade) -> None:
        await self.clock.paging()
        window = await book.get_current_window()
        if book.paragraphs and window.end >= len(book.paragraphs) - 1:
            await book.show_chapter(book.chapter_idx + 1)
            target = 0
        else:
            target = window.end + 1
        await book.update_progress(target, scroll_pct=0.0)
        self.evidence.record_user(
            UserInteraction(
                action="page_down_or_next_chapter",
                arguments={"book_id": book.id, "chapter_idx": book.chapter_idx},
                correlation=book.client.correlation,
            )
        )

    async def page_up(self, book: BookFacade) -> None:
        await self.clock.paging()
        window = await book.get_current_window()
        await book.update_progress(max(0, window.start - 1), scroll_pct=0.0)
        self.evidence.record_user(
            UserInteraction(
                action="page_up",
                arguments={"book_id": book.id, "chapter_idx": book.chapter_idx},
                correlation=book.client.correlation,
            )
        )

    async def wait_for_chat_response(self, response: ChatResponse) -> ChatResponse:
        started = time.monotonic()
        await self.clock.waiting(0)
        self.evidence.record_user(
            UserInteraction(
                action="wait_for_chat_response",
                arguments={"has_error": response.error is not None},
                correlation=(
                    response.events[-1].correlation
                    if response.events
                    else Correlation(run_id="")
                ),
                duration_ms=(time.monotonic() - started) * 1000,
                outcome={"event_count": len(response.events), "error": response.error},
            )
        )
        return response

    async def wait_until(
        self,
        description: str,
        probe: Any,
        *,
        timeout_s: float | None = None,
        required: bool = True,
        correlation: Correlation | None = None,
        accept: Callable[[Any], bool] | None = None,
    ) -> Any:
        started = time.monotonic()
        deadline = started + (
            self.clock.patience_s() if timeout_s is None else timeout_s
        )
        is_accepted = accept or bool
        last: Any = None
        while True:
            candidate = probe()
            last = await candidate if inspect.isawaitable(candidate) else candidate
            if is_accepted(last):
                self._record_wait_until(
                    description, started, "observed", last, correlation
                )
                return last
            if time.monotonic() >= deadline:
                if required:
                    self._record_wait_until(
                        description, started, "timeout", last, correlation
                    )
                    raise TimeoutError(f"timed out waiting for {description}")
                self._record_wait_until(
                    description, started, "not_observed", last, correlation
                )
                return last
            await self.clock.polling()

    def _record_wait_until(
        self,
        description: str,
        started: float,
        outcome: str,
        last: Any,
        correlation: Correlation | None,
    ) -> None:
        self.evidence.record_user(
            UserInteraction(
                action="wait_until",
                arguments={"description": description},
                correlation=correlation or Correlation(run_id=""),
                duration_ms=(time.monotonic() - started) * 1000,
                outcome={"status": outcome, "last": summarize_body(last)},
            )
        )


async def iter_sse(
    lines: AsyncIterator[str],
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    event_type = "message"
    data_lines: list[str] = []
    async for line in lines:
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif not line and data_lines:
            raw = "\n".join(data_lines)
            if raw and raw != "[DONE]":
                yield event_type, json.loads(raw)
            event_type = "message"
            data_lines = []


def parse_response_body(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return response.json()
    return response.text


def summarize_body(body: Any, *, limit: int = 500) -> Any:
    if body is None:
        return None
    if isinstance(body, str):
        return body[:limit]
    if isinstance(body, bytes):
        return {"bytes": len(body)}
    raw = json.dumps(body, ensure_ascii=False, default=str)
    if len(raw) <= limit:
        return body
    return {"excerpt": raw[:limit], "truncated": True}


def parse_imported_book(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("book"), dict):
        return body["book"]
    if isinstance(body, dict) and "id" in body:
        return body
    raise KeyError("import response missing book")


def parse_items(body: Any, *, preferred_key: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return list(body)
    if not isinstance(body, dict):
        raise TypeError(f"expected list response or dict envelope, got {type(body)}")
    for key in ("items", preferred_key):
        if isinstance(body.get(key), list):
            return list(body[key])
    raise KeyError(f"response missing items/{preferred_key}")


def parse_current_window_response(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("window"), dict):
        return body["window"]
    if isinstance(body, dict) and "start_paragraph_idx" in body:
        return body
    raise KeyError("window response missing window")


def response_correlation(
    base: Correlation, headers: httpx.Headers | dict[str, str]
) -> Correlation:
    return replace(
        base,
        trace_id=headers.get("x-trace-id", base.trace_id),
        request_id=headers.get("x-request-id", base.request_id),
    )


def correlation_from_data(base: Correlation, data: dict[str, Any]) -> Correlation:
    return merge_correlation(base, data)


def unwrap(body: Any) -> Any:
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def require_success(response: APIResponse) -> None:
    if response.status_code >= 400:
        raise RuntimeError(f"backend HTTP {response.status_code}: {response.body}")
