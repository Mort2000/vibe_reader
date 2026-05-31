from __future__ import annotations

import json

import httpx
import pytest

from vibe_verify.driver import (
    AppFacade,
    EventSubscriber,
    TargetClient,
    UserFacade,
    iter_sse,
)
from vibe_verify.evidence import EvidenceHub
from vibe_verify.models import Correlation


class InstantClock:
    def __init__(self) -> None:
        self.reads: list[int] = []
        self.waits: list[float] = []
        self.polls = 0

    async def reading(self, paragraphs: int) -> None:
        self.reads.append(paragraphs)

    async def paging(self) -> None:
        self.waits.append(-1)

    async def waiting(self, seconds: float) -> None:
        self.waits.append(seconds)

    async def polling(self) -> None:
        self.polls += 1

    def patience_s(self) -> float:
        return 0.1


def backend(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    headers = {"x-trace-id": "trace", "x-request-id": "request"}
    if path == "/api/books/import":
        return httpx.Response(
            200,
            json={
                "book": {"id": 7, "title": "book"},
                "first_chapter": {"idx": 0},
                "import_stats": {"paragraph_count": 4},
            },
            headers=headers,
        )
    if path == "/api/books/7/chapters/0/paragraphs":
        return httpx.Response(
            200,
            json={
                "book_id": 7,
                "chapter_idx": 0,
                "items": [{"idx": 0}, {"idx": 1}, {"idx": 2}, {"idx": 3}],
                "total": 4,
            },
            headers=headers,
        )
    if path == "/api/books/7/chapters/1/paragraphs":
        return httpx.Response(
            200,
            json={"book_id": 7, "chapter_idx": 1, "items": [{"idx": 0}], "total": 1},
            headers=headers,
        )
    if path == "/api/books/7/progress":
        assert "scroll_pct" in json.loads(request.content)
        return httpx.Response(200, json={"data": {"ok": True}}, headers=headers)
    if path.endswith("/windows/current"):
        return httpx.Response(
            200,
            json={
                "window": {
                    "id": 11,
                    "start_paragraph_idx": 1,
                    "end_paragraph_idx": 3,
                    "focus_start_paragraph_idx": 1,
                    "focus_end_paragraph_idx": 3,
                    "status": "done",
                },
                "comments_ready_count": 1,
                "comments_target_count": 3,
            },
            headers=headers,
        )
    if path.endswith("/comments"):
        assert request.url.params["start"] == "1"
        assert request.url.params["end"] == "3"
        return httpx.Response(
            200,
            json={"book_id": 7, "chapter_idx": 0, "items": [{"paragraph_idx": 2}]},
            headers=headers,
        )
    if path == "/api/windows/5/retry":
        return httpx.Response(
            200,
            json={"window_id": 5, "status": "pending"},
            headers=headers,
        )
    if path == "/api/chat/stream":
        content = (
            'event: chat.started\ndata: {"session_id": 1}\n\n'
            'event: chat.delta\ndata: {"delta": "ans"}\n\n'
            "event: chat.done\n"
            'data: {"ai_msg": "answer", "tokens_in": 3, "tokens_out": 2}\n\n'
        )
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": "text/event-stream", **headers},
        )
    return httpx.Response(404, json={"error": "not found"})


def make_client(hub: EvidenceHub) -> TargetClient:
    client = httpx.AsyncClient(
        base_url="http://backend",
        transport=httpx.MockTransport(backend),
    )
    return TargetClient(
        "http://backend",
        evidence=hub,
        correlation=Correlation(run_id="run"),
        client=client,
    )


async def test_app_book_user_facades_drive_formal_api(tmp_path) -> None:
    corpus = tmp_path / "book.epub"
    corpus.write_bytes(b"epub")
    hub = EvidenceHub()
    target = make_client(hub)
    clock = InstantClock()
    app = AppFacade(target, clock=clock, evidence=hub)
    user = UserFacade(clock=clock, evidence=hub)

    async with app.import_epub(corpus) as book:
        await user.open_chapter(book, 0)
        await user.read_until(book, 2)
        assert book.get_proceeded_paragraph_num() == 3
        window = await book.wait_for_current_window_ready(user)
        assert window.end == 3
        assert window.status == "done"
        assert window.focus_start == 1
        assert window.focus_end == 3
        assert window.identity == 11
        assert window.is_ready is True
        assert (await book.wait_for_comments(user, 1, 3))[0]["paragraph_idx"] == 2
        assert (
            await book.wait_for_comments(
                user, 1, 3, minimum=2, timeout_s=0.001, required=False
            )
        ) == []
        retry = await book.retry_window(5)
        assert retry.body["status"] == "pending"
        await user.page_up(book)
        await user.page_down_or_next_chapter(book)
        assert book.chapter_idx == 1
        response = await app.chat(book, paragraph_idx=0, message="why")
        await user.wait_for_chat_response(response)
        assert response.text == "answer"
        assert response.tokens_in == 3

    assert clock.reads == [2]
    assert clock.waits == [-1, -1, 0]
    assert any(item.action == "retry_window" for item in hub.user_interactions)
    assert len(hub.api_interactions) >= 9
    assert hub.api_interactions[0].correlation.trace_id == "trace"
    assert [event.event_type for event in hub.sse_events[-3:]] == [
        "chat.started",
        "chat.delta",
        "chat.done",
    ]
    await target.close()
    await target._client.aclose()


async def test_user_wait_until_times_out_when_required() -> None:
    hub = EvidenceHub()
    target = make_client(hub)
    user = UserFacade(clock=InstantClock(), evidence=hub)

    with pytest.raises(TimeoutError, match="timed out waiting for impossible"):
        await user.wait_until(
            "impossible",
            lambda: False,
            timeout_s=0.001,
            correlation=target.correlation,
        )

    assert hub.user_interactions[-1].action == "wait_until"
    assert hub.user_interactions[-1].outcome["status"] == "timeout"
    await target._client.aclose()


async def test_user_wait_until_accepts_falsey_completed_value() -> None:
    hub = EvidenceHub()
    target = make_client(hub)
    user = UserFacade(clock=InstantClock(), evidence=hub)

    result = await user.wait_until(
        "zero is ready",
        lambda: 0,
        accept=lambda value: value == 0,
        timeout_s=0.001,
        correlation=target.correlation,
    )

    assert result == 0
    assert hub.user_interactions[-1].outcome["status"] == "observed"
    assert hub.user_interactions[-1].outcome["last"] == 0
    await target._client.aclose()


async def line_source(lines: list[str]):
    for line in lines:
        yield line


async def test_event_subscriber_collect_wait_and_timeout() -> None:
    hub = EvidenceHub()
    subscriber = EventSubscriber(hub, Correlation(run_id="run"))
    await subscriber.collect(
        line_source(["event: done", 'data: {"job_id": "4", "book_id": "7"}', ""])
    )
    event = await subscriber.wait_for("done", timeout_s=0.1)
    assert event.correlation.job_id == 4
    assert event.correlation.book_id == 7
    with pytest.raises(TimeoutError):
        await subscriber.wait_for("missing", timeout_s=0.01)


async def test_event_subscriber_subscribe_uses_formal_events_endpoint() -> None:
    hub = EvidenceHub()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/events"
        assert request.headers["x-verify-run-id"] == "run"
        return httpx.Response(
            200,
            content='event: ready\ndata: {"trace_id": "t"}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(
        base_url="http://backend", transport=httpx.MockTransport(handler)
    )
    subscriber = EventSubscriber(hub, Correlation(run_id="run"))
    await subscriber.subscribe("http://backend", client=client)
    assert subscriber.events[0].event_type == "ready"
    await client.aclose()


async def test_app_subscribe_events_scopes_formal_event_stream() -> None:
    hub = EvidenceHub()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/events"
        assert request.url.params["book_id"] == "7"
        assert request.url.params["chapter_idx"] == "1"
        return httpx.Response(
            200,
            content=(
                "event: context.compacted\n"
                'data: {"trace_id": "t", "book_id": 7, "chapter_idx": 1}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    target = TargetClient(
        "http://backend",
        evidence=hub,
        correlation=Correlation(run_id="run"),
        client=httpx.AsyncClient(
            base_url="http://backend", transport=httpx.MockTransport(handler)
        ),
    )
    app = AppFacade(target, clock=InstantClock(), evidence=hub)

    async with app.subscribe_events(book_id=7, chapter_idx=1) as events:
        observed = await events.wait_for("context.compacted", timeout_s=0.1)

    assert observed.correlation.book_id == 7
    assert hub.api_interactions[-1].path == "/api/events"
    await target._client.aclose()


async def test_app_subscribe_events_fails_when_formal_stream_unavailable() -> None:
    hub = EvidenceHub()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/events"
        return httpx.Response(
            500,
            json={"error": "broken"},
        )

    target = TargetClient(
        "http://backend",
        evidence=hub,
        correlation=Correlation(run_id="run"),
        client=httpx.AsyncClient(
            base_url="http://backend", transport=httpx.MockTransport(handler)
        ),
    )
    app = AppFacade(target, clock=InstantClock(), evidence=hub)

    with pytest.raises(RuntimeError, match="/api/events HTTP 500"):
        async with app.subscribe_events(book_id=7, chapter_idx=1):
            pass

    assert hub.api_interactions[-1].path == "/api/events"
    assert hub.api_interactions[-1].status_code == 500
    await target._client.aclose()


async def test_iter_sse_ignores_done_marker() -> None:
    events = [
        item
        async for item in iter_sse(
            line_source(
                [
                    "event: ping",
                    "data: ",
                    "",
                    "data: " + json.dumps({"x": 1}),
                    "",
                    "data: [DONE]",
                    "",
                ]
            )
        )
    ]
    assert events == [("message", {"x": 1})]


async def test_request_records_redacted_headers() -> None:
    hub = EvidenceHub()
    target = make_client(hub)
    await target.request("GET", "/missing", headers={"Authorization": "Bearer secret"})
    assert hub.api_interactions[0].request_headers["Authorization"] == "***REDACTED***"
    await target._client.aclose()


async def test_stream_chat_records_api_evidence_on_sse_parse_failure() -> None:
    hub = EvidenceHub()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat/stream"
        return httpx.Response(
            200,
            content="event: chat.delta\ndata: not-json\n\n",
            headers={"content-type": "text/event-stream"},
        )

    client = TargetClient(
        "http://backend",
        evidence=hub,
        correlation=Correlation(run_id="run"),
        client=httpx.AsyncClient(
            base_url="http://backend",
            transport=httpx.MockTransport(handler),
        ),
    )

    with pytest.raises(json.JSONDecodeError):
        await client.stream_chat({"book_id": 1, "user_msg": "x"})

    assert hub.api_interactions[-1].path == "/api/chat/stream"
    assert hub.api_interactions[-1].status_code == 200
    assert hub.api_interactions[-1].error
    await client._client.aclose()
