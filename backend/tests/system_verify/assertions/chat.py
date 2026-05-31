"""Chat SSE and continuity assertions (V-10 / V-11)."""

from __future__ import annotations

import re
from typing import Any

from ..core.client_factory import ChatStreamResult
from ..core.config import VerifyConfig
from ..core.scenario import StepAssertionError, assert_that

FORBIDDEN_CHAT_REQUEST_FIELDS = frozenset(
    {
        "selection",
        "span_start",
        "span_end",
        "selected_text",
    }
)

STUB_CHAT_MARKER = re.compile(r"\[stub:[^\]]+\]\[chat\]", re.IGNORECASE)
ANCHOR_MARKER = re.compile(r"anchor=P\d+", re.IGNORECASE)


def assert_chat_request_has_no_selection(body: dict[str, Any]) -> None:
    """Request body must not carry selection / span fields."""
    for field in FORBIDDEN_CHAT_REQUEST_FIELDS:
        assert_that.not_contains(
            body,
            field,
            label=f"chat_request_must_not_include_{field}",
        )


def assert_chat_sse_contract(result: ChatStreamResult) -> None:
    """Validate chat.started / chat.delta / chat.done SSE contract."""
    event_types = [event["event_type"] for event in result.events]
    assert_that.is_true(
        "chat.delta" in event_types,
        "Chat stream must emit at least one chat.delta event",
    )
    assert_that.is_true(
        "chat.done" in event_types or result.error is not None,
        "Chat stream must emit chat.done or chat.error",
    )
    if result.error:
        raise StepAssertionError(
            assertion="chat_stream_error",
            message=f"Chat stream failed: {result.error.get('code')} — {result.error.get('message')}",
            actual=result.error,
        )
    assert_that.is_true(
        bool(result.full_text.strip()),
        "Chat response text must be non-empty",
    )


def assert_chat_timing_observable(result: ChatStreamResult) -> None:
    """TTFT and total latency must be measurable."""
    assert_that.is_not_none(result.ttft_ms, "chat.ttft_ms must be observable")
    assert_that.is_not_none(result.total_ms, "chat.total_ms must be observable")
    assert_that.gte(result.ttft_ms or 0, 0, label="chat.ttft_ms_non_negative")
    assert_that.gte(result.total_ms or 0, 0, label="chat.total_ms_non_negative")
    if result.ttft_ms is not None and result.total_ms is not None:
        assert_that.lte(
            result.ttft_ms,
            result.total_ms,
            label="chat.ttft_ms_lte_total_ms",
        )


def assert_stub_chat_context_markers(
    result: ChatStreamResult,
    *,
    chapter_idx: int,
    paragraph_idx: int,
    stub_profile: str | None = None,
) -> None:
    """Stub mode responses should expose anchor markers proving context injection."""
    text = result.full_text
    assert_that.is_true(
        bool(STUB_CHAT_MARKER.search(text)),
        "Stub chat response must include [stub:...][chat] marker",
    )
    assert_that.is_true(
        bool(ANCHOR_MARKER.search(text)),
        "Stub chat response must include paragraph anchor marker",
    )
    if stub_profile:
        assert_that.contains(
            text,
            f"[stub:{stub_profile}]",
            label="stub_profile_marker_present",
        )
    assert_that.contains(
        text,
        f"[paragraph={paragraph_idx}]",
        label="paragraph_anchor_in_stub_response",
    )


def assert_followup_continuity(
    first: ChatStreamResult,
    followup: ChatStreamResult,
    *,
    followup_user_msg: str,
) -> None:
    """Follow-up turn should reuse session and produce a non-empty answer."""
    if first.session_id is not None and followup.session_id is not None:
        assert_that.equal(
            followup.session_id,
            first.session_id,
            label="followup_reuses_session_id",
        )
    assert_that.is_true(
        bool(followup.full_text.strip()),
        "Follow-up chat response must be non-empty",
    )
    if followup.full_text.strip() == first.full_text.strip():
        raise StepAssertionError(
            assertion="followup_response_differs_from_first",
            message="Follow-up response should differ from the first answer",
            expected="distinct follow-up answer",
            actual={"first": first.full_text[:120], "followup": followup.full_text[:120]},
        )
    assert_that.is_true(
        bool(followup_user_msg.strip()),
        "Follow-up user message must be non-empty",
    )


def assert_recent_chat_in_injected_context(
    injected_context: dict[str, Any],
    *,
    min_turns: int = 1,
) -> None:
    """Recent chat history should appear in ephemeral recent chat component."""
    component = None
    for candidate in injected_context.get("components") or []:
        name = str(candidate.get("name") or "")
        if name in ("ephemeral_recent_chat", "recent_chat", "chat_history"):
            component = candidate
            break

    assert_that.is_not_none(
        component,
        "Follow-up chat injected context must include recent chat component",
    )
    assert component is not None

    content = component.get("content") or {}
    turns = content.get("turns") or content.get("messages") or []
    if isinstance(turns, list):
        assert_that.gte(
            len(turns),
            min_turns,
            label="recent_chat_turn_count",
        )


def assert_chat_tokens_recorded(result: ChatStreamResult, config: VerifyConfig) -> None:
    """Token usage should be present when provider usage collection is enabled."""
    if config.metrics.collect_provider_usage:
        assert_that.is_not_none(result.tokens_in, "chat.tokens.input should be recorded")
        assert_that.is_not_none(result.tokens_out, "chat.tokens.output should be recorded")
    elif result.tokens_in is None and result.tokens_out is None:
        return
    else:
        assert_that.gte(result.tokens_in or 0, 0, label="chat.tokens.input")
        assert_that.gte(result.tokens_out or 0, 0, label="chat.tokens.output")
