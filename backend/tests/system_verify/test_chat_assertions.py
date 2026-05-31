"""Unit tests for chat assertions."""

from __future__ import annotations

import pytest

from tests.system_verify.assertions.chat import (
    assert_chat_request_has_no_selection,
    assert_chat_sse_contract,
    assert_chat_timing_observable,
    assert_followup_continuity,
    assert_recent_chat_in_injected_context,
    assert_stub_chat_context_markers,
)
from tests.system_verify.core.client_factory import ChatStreamResult
from tests.system_verify.core.scenario import StepAssertionError


def _chat_result(**overrides) -> ChatStreamResult:
    base = ChatStreamResult(
        user_msg="这里为什么有点奇怪？",
        session_id=1,
        turn_id=10,
        trace_id="trace_chat_1",
        ai_msg="[stub:mvp_default][chat][chapter=1][paragraph=24] anchor=P24",
        deltas=["[stub:mvp_default][chat]"],
        events=[
            {"event_type": "chat.started", "data": {"turn_id": 10, "session_id": 1}},
            {"event_type": "chat.delta", "data": {"delta": "[stub:mvp_default][chat]"}},
            {
                "event_type": "chat.done",
                "data": {
                    "turn_id": 10,
                    "ai_msg": "[stub:mvp_default][chat][chapter=1][paragraph=24] anchor=P24",
                    "tokens_in": 120,
                    "tokens_out": 40,
                },
            },
        ],
        ttft_ms=120.0,
        total_ms=980.0,
        tokens_in=120,
        tokens_out=40,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_assert_chat_request_has_no_selection() -> None:
    assert_chat_request_has_no_selection(
        {
            "book_id": 1,
            "chapter_idx": 1,
            "paragraph_idx": 24,
            "user_msg": "hello",
        }
    )
    with pytest.raises(StepAssertionError):
        assert_chat_request_has_no_selection(
            {"book_id": 1, "selection": "bad", "user_msg": "hello"}
        )


def test_assert_chat_sse_contract() -> None:
    assert_chat_sse_contract(_chat_result())
    with pytest.raises(StepAssertionError):
        assert_chat_sse_contract(_chat_result(events=[], deltas=[]))


def test_assert_chat_timing_observable() -> None:
    assert_chat_timing_observable(_chat_result())


def test_assert_stub_chat_context_markers() -> None:
    assert_stub_chat_context_markers(
        _chat_result(),
        chapter_idx=1,
        paragraph_idx=24,
        stub_profile="mvp_default",
    )


def test_assert_followup_continuity() -> None:
    first = _chat_result(session_id=5)
    followup = _chat_result(
        session_id=5,
        ai_msg="follow-up answer about pressure",
        user_msg="你刚才说的压迫感是从哪里来的？",
    )
    assert_followup_continuity(
        first,
        followup,
        followup_user_msg="你刚才说的压迫感是从哪里来的？",
    )


def test_assert_recent_chat_in_injected_context() -> None:
    assert_recent_chat_in_injected_context(
        {
            "components": [
                {
                    "name": "ephemeral_recent_chat",
                    "content": {
                        "turns": [
                            {"role": "user", "text": "q1"},
                            {"role": "assistant", "text": "a1"},
                        ]
                    },
                }
            ]
        },
        min_turns=1,
    )
