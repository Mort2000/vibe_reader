from __future__ import annotations

import pytest

from app.infrastructure.events import SSEEventPublisher
from app.observability import set_request_context


@pytest.mark.asyncio
async def test_sse_publisher_adds_current_correlation_fields() -> None:
    set_request_context(
        request_id="req_test",
        trace_id="trace_test",
        verify_run_id="run_test",
        verify_scenario_id="scenario_test",
        verify_step_id="step_test",
    )
    publisher = SSEEventPublisher()
    queue = publisher.subscribe()
    try:
        await publisher.publish("window.queued", {"book_id": 1, "chapter_idx": 2})

        event = await queue.get()

        assert event["request_id"] == "req_test"
        assert event["trace_id"] == "trace_test"
        assert event["verify_run_id"] == "run_test"
        assert event["verify_scenario_id"] == "scenario_test"
        assert event["verify_step_id"] == "step_test"
        assert event["book_id"] == 1
        assert event["chapter_idx"] == 2
    finally:
        publisher.unsubscribe(queue)
