"""Unit tests for chat assertions."""

from __future__ import annotations

import pytest

from tests.system_verify.assertions.chat import (
    assert_chat_live_l2_grounded,
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


def _agent_run_from_manifest(
    *,
    live_tokens: int,
    live_chunk_ids: list[int],
    prompt: str,
    context_degraded: bool = False,
    raw_total: int = 5000,
    total_estimate: int = 5000,
) -> dict:
    return {
        "interaction": {
            "prompt_manifest": {
                "components": [
                    {"name": "live_original_chunks", "tokens": live_tokens},
                ],
                "live_chunk_ids": live_chunk_ids,
                "context_degraded": context_degraded,
                "raw_total_estimate": raw_total,
                "total_estimate": total_estimate,
            },
            "prompt_messages": [{"role": "user", "content": prompt}],
        }
    }


def test_assert_chat_live_l2_grounded_passes_with_paragraph_markers() -> None:
    prompt = "\n".join(
        [
            "<LIVE_ORIGINAL_CHUNKS>",
            "<CHUNK seq=0 start_p=0 end_p=80>",
            "[p=20] 段落正文。",
            "</CHUNK>",
            "</LIVE_ORIGINAL_CHUNKS>",
        ]
    )
    assert_chat_live_l2_grounded(
        _agent_run_from_manifest(
            live_tokens=120,
            live_chunk_ids=[1],
            prompt=prompt,
        ),
        reading_paragraph_idx=20,
    )


def test_assert_chat_live_l2_grounded_fails_when_live_block_empty() -> None:
    prompt = (
        "<LIVE_ORIGINAL_CHUNKS>\n</LIVE_ORIGINAL_CHUNKS>\n"
        "<CURRENT_TASK>\ncurrent_reading_paragraph_idx = 20\nmode = chat\n"
        "</CURRENT_TASK>"
    )
    with pytest.raises(StepAssertionError) as exc_info:
        assert_chat_live_l2_grounded(
            _agent_run_from_manifest(
                live_tokens=0,
                live_chunk_ids=[],
                prompt=prompt,
                raw_total=137,
                total_estimate=15819,
            ),
            reading_paragraph_idx=20,
        )
    assert exc_info.value.assertion == "chat_live_l2_grounded"


def test_assert_chat_live_l2_grounded_fails_when_reading_paragraph_missing() -> None:
    prompt = "\n".join(
        [
            "<LIVE_ORIGINAL_CHUNKS>",
            "<CHUNK seq=1 start_p=180 end_p=359>",
            "[p=200] 远处段落。",
            "</CHUNK>",
            "</LIVE_ORIGINAL_CHUNKS>",
        ]
    )
    with pytest.raises(StepAssertionError) as exc_info:
        assert_chat_live_l2_grounded(
            _agent_run_from_manifest(
                live_tokens=800,
                live_chunk_ids=[3],
                prompt=prompt,
            ),
            reading_paragraph_idx=20,
        )
    assert exc_info.value.assertion == "chat_reading_paragraph_in_live_l2"


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
