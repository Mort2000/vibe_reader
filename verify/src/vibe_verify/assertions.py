"""Pure reusable assertions for scenario scripts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .models import AgentInvocation, APIInteraction, SSEEvent, TokenUsage

COMMENT_TYPES = {"observation", "question", "craft", "humor", "warning"}
CHAT_TERMINAL_EVENTS = {"chat.done", "chat.error"}
CHAT_SELECTION_KEYS = {
    "selection",
    "selectedtext",
    "selectedrange",
    "selectedspan",
    "selectionstart",
    "selectionend",
    "selected_text",
    "selected_range",
    "selected_span",
    "selection_start",
    "selection_end",
    "span",
    "spanstart",
    "spanend",
    "sourcespan",
    "span_start",
    "span_end",
    "source_span",
}
CHAT_NON_TERMINAL_EVENTS = {"chat.started", "chat.first_delta", "chat.delta"}


def fail(message: str, **fields: Any) -> None:
    detail = " ".join(f"{key}={value!r}" for key, value in fields.items())
    raise AssertionError(f"{message}" + (f" ({detail})" if detail else ""))


def check_response_status(status_code: int, *, expected: int = 200) -> None:
    if status_code != expected:
        fail("unexpected HTTP status", expected=expected, actual=status_code)


def check_sse_sequence(events: Iterable[SSEEvent], expected: list[str]) -> None:
    actual = [event.event_type for event in events]
    if actual != expected:
        fail("unexpected SSE sequence", expected=expected, actual=actual)


def check_available_count(label: str, *, requested: int, available: int) -> None:
    if requested > available:
        fail(
            f"not enough {label}",
            expected=f">={requested}",
            actual=available,
        )


def check_comments(
    comments: Iterable[dict[str, Any]],
    *,
    start: int,
    end: int,
    minimum: int = 1,
) -> None:
    items = list(comments)
    if len(items) < minimum:
        fail("not enough comments", expected=f">={minimum}", actual=len(items))
    ids: set[Any] = set()
    for index, comment in enumerate(items):
        if not isinstance(comment, dict):
            fail(
                "comment must be an object", index=index, actual=type(comment).__name__
            )
        paragraph_idx = comment.get("paragraph_idx")
        if not isinstance(paragraph_idx, int):
            fail(
                "comment paragraph_idx must be int",
                index=index,
                actual=paragraph_idx,
                range=f"{start}..={end}",
            )
        if not start <= paragraph_idx <= end:
            fail(
                "comment outside range",
                index=index,
                expected=f"{start}..={end}",
                actual=paragraph_idx,
            )
        comment_type = comment.get("comment_type")
        if comment_type not in COMMENT_TYPES:
            fail(
                "invalid comment type",
                index=index,
                expected=sorted(COMMENT_TYPES),
                actual=comment_type,
            )
        if not str(comment.get("comment", "")).strip():
            fail("comment text is empty", index=index, paragraph_idx=paragraph_idx)
        if "id" in comment:
            if comment["id"] in ids:
                fail("duplicate comment id", index=index, actual=comment["id"])
            ids.add(comment["id"])


def check_chat_response(response: Any) -> None:
    if getattr(response, "error", None):
        fail("chat failed", actual=response.error)
    if not getattr(response, "text", "").strip():
        fail("chat response is empty")
    ttft_ms = getattr(response, "ttft_ms", None)
    duration_ms = getattr(response, "duration_ms", None)
    if ttft_ms is None:
        fail("chat TTFT missing")
    if duration_ms is None:
        fail("chat duration missing")
    if ttft_ms < 0 or duration_ms < 0 or duration_ms < ttft_ms:
        fail("invalid chat timing", ttft_ms=ttft_ms, duration_ms=duration_ms)
    events = list(getattr(response, "events", []) or [])
    if not events:
        fail("chat stream events missing")
    actual = [event.event_type for event in events]
    if "chat.error" in actual:
        fail("chat.error event observed", actual=actual)
    for event_type in ("chat.started", "chat.delta", "chat.done"):
        if event_type not in actual:
            fail("chat stream missing event", expected=event_type, actual=actual)
    check_chat_sse_contract(events)


def check_chat_usage(response: Any) -> None:
    """Require chat usage fields to be present on the public SSE terminal event."""
    tokens_in = getattr(response, "tokens_in", None)
    tokens_out = getattr(response, "tokens_out", None)
    usage_source = getattr(response, "usage_source", "")
    if tokens_in is None:
        fail("chat input token usage missing")
    if tokens_out is None:
        fail("chat output token usage missing")
    if tokens_in <= 0 or tokens_out <= 0:
        fail(
            "chat token usage must be positive",
            input=tokens_in,
            output=tokens_out,
        )
    if usage_source not in {"sse", "provider", "framework", "estimate"}:
        fail("chat usage source missing", actual=usage_source)


def check_chat_sse_contract(
    events: Iterable[SSEEvent],
    *,
    allow_error: bool = False,
) -> None:
    """Validate streamed chat event ordering without depending on text content."""
    items = list(events)
    actual = [event.event_type for event in items]
    if not actual:
        fail("chat SSE events missing")
    unknown = [
        event_type
        for event_type in actual
        if event_type.startswith("chat.")
        and event_type not in CHAT_NON_TERMINAL_EVENTS
        and event_type not in CHAT_TERMINAL_EVENTS
    ]
    if unknown:
        fail("unexpected chat SSE event", actual=actual, unexpected=unknown)
    if "chat.started" not in actual:
        fail("chat stream missing event", expected="chat.started", actual=actual)
    if actual.count("chat.started") != 1:
        fail("chat stream must contain exactly one started event", actual=actual)
    if actual[0] != "chat.started":
        fail("chat stream must start with chat.started", actual=actual)
    terminal_indices = [
        index
        for index, event_type in enumerate(actual)
        if event_type in CHAT_TERMINAL_EVENTS
    ]
    if len(terminal_indices) != 1:
        fail("chat stream must contain exactly one terminal event", actual=actual)
    terminal_index = terminal_indices[0]
    terminal = actual[terminal_index]
    if terminal == "chat.error" and not allow_error:
        fail("chat.error event observed", actual=actual)
    if terminal == "chat.done" and "chat.delta" not in actual[:terminal_index]:
        fail("chat.done observed before any chat.delta", actual=actual)
    if any(
        event_type == "chat.started"
        for event_type in actual[1:terminal_index]
    ):
        fail("chat.started repeated before terminal event", actual=actual)
    trailing = actual[terminal_index + 1 :]
    if any(event_type.startswith("chat.") for event_type in trailing):
        fail("chat events observed after terminal event", actual=actual)


def check_chat_requests_without_selection(
    interactions: Iterable[APIInteraction],
    *,
    minimum: int = 1,
) -> None:
    """Ensure formal chat requests are anchored only by book/chapter/paragraph."""
    chat_requests = [item for item in interactions if item.path == "/api/chat/stream"]
    if len(chat_requests) < minimum:
        fail(
            "not enough chat API requests",
            expected=f">={minimum}",
            actual=len(chat_requests),
        )
    for index, request in enumerate(chat_requests):
        keys = payload_keys(request.request_body)
        forbidden = sorted(key for key in keys if is_selection_key(key))
        if forbidden:
            fail(
                "chat request contains selection/span fields",
                index=index,
                keys=forbidden,
            )


def check_followup_session(first: Any, followup: Any) -> None:
    first_session = getattr(first, "session_id", None)
    followup_session = getattr(followup, "session_id", None)
    if first_session is None:
        fail("first chat session_id missing")
    if followup_session is None:
        fail("followup chat session_id missing")
    if first_session != followup_session:
        fail(
            "followup did not reuse chat session",
            expected=first_session,
            actual=followup_session,
        )
    first_turn = getattr(first, "turn_id", None)
    followup_turn = getattr(followup, "turn_id", None)
    if (
        first_turn is not None
        and followup_turn is not None
        and followup_turn <= first_turn
    ):
        fail(
            "followup turn did not advance",
            first_turn=first_turn,
            followup_turn=followup_turn,
        )


def payload_keys(value: Any) -> set[str]:
    """Extract request keys from raw or sanitized payload summaries."""
    if not isinstance(value, dict):
        return set()
    result: set[str] = set()
    for summary_key in ("keys", "deep_keys"):
        keys = value.get(summary_key)
        if isinstance(keys, list):
            result.update(str(key) for key in keys)
    for key, nested in value.items():
        result.add(str(key))
        if isinstance(nested, dict):
            result.update(payload_keys(nested))
        elif isinstance(nested, list):
            for item in nested:
                result.update(payload_keys(item))
    return result


def is_selection_key(key: str) -> bool:
    normalized = key.replace("_", "").replace("-", "").lower()
    return normalized in {item.replace("_", "").lower() for item in CHAT_SELECTION_KEYS}


def check_prompt_contains(invocation: AgentInvocation, *fragments: str) -> None:
    for fragment in fragments:
        if fragment not in invocation.prompt:
            fail(
                "prompt missing fragment",
                expected=fragment,
                invocation_id=invocation.id,
                trace_id=invocation.correlation.trace_id,
            )


def check_token_usage(
    usage: TokenUsage,
    *,
    max_total: int | None = None,
    allowed_sources: set[str] | None = None,
) -> None:
    if usage.input < 0 or usage.output < 0 or usage.cached_input < 0:
        fail("token usage must be non-negative", actual=usage.to_dict())
    if max_total is not None and usage.total > max_total:
        fail("token budget exceeded", expected=f"<={max_total}", actual=usage.total)
    if allowed_sources is not None and usage.source not in allowed_sources:
        fail(
            "unexpected usage source",
            expected=sorted(allowed_sources),
            actual=usage.source,
        )


def check_audit_invocation(invocation: AgentInvocation) -> None:
    if not invocation.id:
        fail("agent invocation id missing", trace_id=invocation.correlation.trace_id)
    if not invocation.agent:
        fail("agent name missing", invocation_id=invocation.id)
    if not invocation.prompt_messages:
        fail("prompt messages missing", invocation_id=invocation.id)
    check_token_usage(invocation.usage)


def check_agent_coverage(
    invocations: Iterable[AgentInvocation],
    *,
    required_agents: Iterable[str],
) -> None:
    items = list(invocations)
    observed = {item.agent for item in items}
    missing = [agent for agent in required_agents if agent not in observed]
    if missing:
        fail("required agent calls missing", expected=missing, actual=sorted(observed))
    for item in items:
        check_audit_invocation(item)


def check_compaction_summary_reused(
    invocations: Iterable[AgentInvocation],
    *,
    chat_agent: str = "ReadingChatAgent",
    compaction_agent: str = "ContextCompactionAgent",
) -> None:
    items = list(invocations)
    compactions = [item for item in items if item.agent == compaction_agent]
    chats = [item for item in items if item.agent == chat_agent]
    if not compactions:
        fail("compaction agent call missing")
    if not chats:
        fail("chat agent call missing")
    summary_source, summary_text = last_compaction_summary(compactions)
    prompt = chats[-1].prompt
    if not summary_text:
        fail(
            "compaction response did not expose summary text",
            compaction_invocation_id=compactions[-1].id,
        )
    if summary_visible_in_prompt(summary_text, prompt):
        return
    fail(
        "compaction summary not visible in subsequent chat context",
        summary_excerpt=summary_text[:80],
        summary_hash=summary_hash(summary_text),
        compaction_invocation_id=summary_source.id,
        chat_invocation_id=chats[-1].id,
    )


def last_compaction_summary(
    compactions: list[AgentInvocation],
) -> tuple[AgentInvocation, str]:
    for item in reversed(compactions):
        summary = extract_summary_text(item.response)
        if summary:
            return item, summary
    return compactions[-1], ""


def summary_visible_in_prompt(summary: str, prompt: str) -> bool:
    normalized_summary = normalize_text(summary)
    normalized_prompt = normalize_text(prompt)
    if stable_summary_fragment(normalized_summary) in normalized_prompt:
        return True
    return summary_hash(summary) in prompt


def stable_summary_fragment(summary: str) -> str:
    if len(summary) <= 240:
        return summary
    return summary[:240]


def summary_hash(summary: str) -> str:
    return "sha256:" + hashlib.sha256(summary.encode("utf-8")).hexdigest()


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def extract_summary_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ""
    if not isinstance(value, dict):
        return ""
    for key in ("chapter_compressed_summary", "chapter_summary", "summary_payload"):
        nested = value.get(key)
        if isinstance(nested, dict) and str(nested.get("summary", "")).strip():
            return str(nested["summary"])
    if str(value.get("summary", "")).strip():
        return str(value["summary"])
    choices = value.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return ""
            return extract_summary_text(parsed)
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                function = (
                    tool_call.get("function") if isinstance(tool_call, dict) else None
                )
                arguments = (
                    function.get("arguments") if isinstance(function, dict) else None
                )
                if not isinstance(arguments, str):
                    continue
                try:
                    parsed = json.loads(arguments)
                except json.JSONDecodeError:
                    continue
                summary = extract_summary_text(parsed.get("payload", parsed))
                if summary:
                    return summary
    return ""
