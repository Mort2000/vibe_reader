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
LIVE_ORIGINAL_BLOCK = re.compile(
    r"<LIVE_ORIGINAL_CHUNKS>(.*?)</LIVE_ORIGINAL_CHUNKS>",
    re.DOTALL | re.IGNORECASE,
)
L2_PARAGRAPH_MARKER = re.compile(r"\[p=(\d+)\]")
L2_CHUNK_MARKER = re.compile(r"<CHUNK\s", re.IGNORECASE)


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


def _manifest_component_tokens(manifest: dict[str, Any], name: str) -> int:
    for component in manifest.get("components") or []:
        if str(component.get("name") or "") == name:
            return int(component.get("tokens") or 0)
    return 0


def _prompt_text_from_agent_run(agent_run: dict[str, Any]) -> str:
    interaction = agent_run.get("interaction") or agent_run
    parts: list[str] = []
    for message in interaction.get("prompt_messages") or []:
        if str(message.get("role") or "") == "user":
            parts.append(str(message.get("content") or ""))
    if parts:
        return "\n".join(parts)
    return str(interaction.get("prompt") or "")


def _live_original_block_text(prompt: str) -> str:
    match = LIVE_ORIGINAL_BLOCK.search(prompt)
    return match.group(1) if match else ""


def assert_chat_live_l2_grounded(
    agent_run: dict[str, Any],
    *,
    reading_paragraph_idx: int,
) -> None:
    """Direct chat must inject non-empty L2 original text at the reading position.

    Catches the case where compaction reclaimed early chunks but the reader is
    still before the new live_start, leaving an empty LIVE_ORIGINAL block while
    ``context_degraded`` remains false and ``total_estimate`` is inflated by
    reserved budget alone.
    """
    interaction = agent_run.get("interaction") or agent_run
    manifest = interaction.get("prompt_manifest") or {}
    prompt = _prompt_text_from_agent_run(agent_run)
    live_block = _live_original_block_text(prompt)
    live_tokens = _manifest_component_tokens(manifest, "live_original_chunks")
    live_chunk_ids = manifest.get("live_chunk_ids") or []
    context_degraded = bool(manifest.get("context_degraded"))
    raw_total = int(manifest.get("raw_total_estimate") or 0)
    total_estimate = int(manifest.get("total_estimate") or 0)

    paragraph_markers = [
        int(match.group(1)) for match in L2_PARAGRAPH_MARKER.finditer(live_block)
    ]
    has_chunk_marker = bool(L2_CHUNK_MARKER.search(live_block))
    has_paragraph_text = bool(paragraph_markers) or has_chunk_marker
    reading_grounded = reading_paragraph_idx in paragraph_markers

    if context_degraded:
        return

    if live_tokens <= 0 or not live_chunk_ids or not has_paragraph_text:
        raise StepAssertionError(
            assertion="chat_live_l2_grounded",
            message=(
                "Chat prompt must include non-empty LIVE_ORIGINAL_CHUNKS near the "
                "reading position when context is not degraded"
            ),
            expected={
                "live_original_tokens_gt": 0,
                "live_chunk_ids_non_empty": True,
                "prompt_contains_paragraph_markers": True,
                "reading_paragraph_idx": reading_paragraph_idx,
            },
            actual={
                "live_original_tokens": live_tokens,
                "live_chunk_ids": live_chunk_ids,
                "context_degraded": context_degraded,
                "live_block_excerpt": live_block[:240],
                "paragraph_markers": paragraph_markers[:12],
            },
        )

    if not reading_grounded:
        raise StepAssertionError(
            assertion="chat_reading_paragraph_in_live_l2",
            message=(
                "Chat LIVE_ORIGINAL_CHUNKS must include the current reading "
                f"paragraph [p={reading_paragraph_idx}]"
            ),
            expected={"reading_paragraph_idx": reading_paragraph_idx},
            actual={"paragraph_markers": paragraph_markers[:24]},
        )

    if total_estimate > raw_total + 8_000 and raw_total < 2_000:
        raise StepAssertionError(
            assertion="chat_manifest_estimate_not_reserved_only",
            message=(
                "Chat total_estimate must not be dominated by reserved budget when "
                "raw prompt content is nearly empty"
            ),
            expected={"raw_total_estimate_gte": 2_000},
            actual={
                "raw_total_estimate": raw_total,
                "total_estimate": total_estimate,
                "live_original_tokens": live_tokens,
            },
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
