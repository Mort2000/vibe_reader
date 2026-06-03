"""Pure reusable assertions for scenario scripts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .models import AgentInvocation, APIInteraction, SSEEvent, TokenUsage

COMMENT_TYPES = {"observation", "question", "craft", "humor", "warning"}
COMMENT_SPAN_KEYS = {"span", "span_start", "span_end"}
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
        forbidden = sorted(key for key in COMMENT_SPAN_KEYS if key in comment)
        if forbidden:
            fail(
                "comment contains span fields",
                index=index,
                paragraph_idx=paragraph_idx,
                keys=forbidden,
            )
        if "id" in comment:
            if comment["id"] in ids:
                fail("duplicate comment id", index=index, actual=comment["id"])
            ids.add(comment["id"])


def check_paragraphs(
    paragraphs: Iterable[dict[str, Any]],
    *,
    minimum: int = 1,
    expected_start: int | None = None,
    require_text: bool = False,
) -> None:
    items = list(paragraphs)
    if len(items) < minimum:
        fail("not enough paragraphs", expected=f">={minimum}", actual=len(items))

    previous_idx: int | None = None
    for position, paragraph in enumerate(items):
        if not isinstance(paragraph, dict):
            fail(
                "paragraph must be an object",
                position=position,
                actual=type(paragraph).__name__,
            )
        idx = paragraph.get("paragraph_idx", paragraph.get("idx"))
        if not isinstance(idx, int):
            fail("paragraph idx must be int", position=position, actual=idx)
        if position == 0 and expected_start is not None and idx != expected_start:
            fail(
                "paragraph idx does not start at expected value",
                expected=expected_start,
                actual=idx,
            )
        if previous_idx is not None and idx != previous_idx + 1:
            fail(
                "paragraph idx is not contiguous",
                position=position,
                expected=previous_idx + 1,
                actual=idx,
            )
        if require_text and not str(paragraph.get("text", "")).strip():
            fail("paragraph text is empty", position=position, paragraph_idx=idx)
        previous_idx = idx


def check_progress(
    progress: dict[str, Any],
    *,
    book_id: int | None = None,
    chapter_idx: int | None = None,
    paragraph_idx: int | None = None,
) -> None:
    if not isinstance(progress, dict):
        fail("progress must be an object", actual=type(progress).__name__)
    if book_id is not None and progress.get("book_id") != book_id:
        fail(
            "progress book_id mismatch",
            expected=book_id,
            actual=progress.get("book_id"),
        )
    if chapter_idx is not None and progress.get("chapter_idx") != chapter_idx:
        fail(
            "progress chapter_idx mismatch",
            expected=chapter_idx,
            actual=progress.get("chapter_idx"),
        )
    if paragraph_idx is not None and progress.get("paragraph_idx") != paragraph_idx:
        fail(
            "progress paragraph_idx mismatch",
            expected=paragraph_idx,
            actual=progress.get("paragraph_idx"),
        )


def check_window_covers(window: Any, *, paragraph_idx: int) -> None:
    start = getattr(window, "start", None)
    end = getattr(window, "end", None)
    if not isinstance(start, int) or not isinstance(end, int):
        fail("window range must be observable", start=start, end=end)
    if start > end:
        fail("window range is inverted", start=start, end=end)
    if not start <= paragraph_idx <= end:
        fail(
            "window does not cover paragraph",
            expected=f"{start}..={end}",
            actual=paragraph_idx,
        )


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


def check_chat_session_sequence(
    responses: Iterable[Any],
    *,
    minimum: int = 2,
) -> None:
    items = list(responses)
    if len(items) < minimum:
        fail("not enough chat turns", expected=f">={minimum}", actual=len(items))
    previous_session: int | None = None
    previous_turn: int | None = None
    for index, response in enumerate(items):
        session_id = getattr(response, "session_id", None)
        turn_id = getattr(response, "turn_id", None)
        if not isinstance(session_id, int):
            fail("chat session_id missing", index=index, actual=session_id)
        if not isinstance(turn_id, int):
            fail("chat turn_id missing", index=index, actual=turn_id)
        if previous_session is not None and session_id != previous_session:
            fail(
                "followup did not reuse chat session",
                expected=previous_session,
                actual=session_id,
            )
        if previous_turn is not None and turn_id <= previous_turn:
            fail(
                "followup turn did not advance",
                first_turn=previous_turn,
                followup_turn=turn_id,
            )
        previous_session = session_id
        previous_turn = turn_id


def check_chat_prompt_context(call: Any, *, paragraph_idx: int) -> None:
    prompt = str(getattr(call, "prompt", ""))
    if "mode = chat" not in prompt.lower():
        fail(
            "chat prompt missing current-context mode",
            invocation_id=getattr(call, "id", ""),
        )
    marker = f"current_reading_paragraph_idx = {paragraph_idx}"
    if marker not in prompt:
        fail(
            "chat prompt missing current reading paragraph",
            expected=marker,
            invocation_id=getattr(call, "id", ""),
        )


def extract_chat_response_text(value: Any) -> str:
    if isinstance(value, dict):
        if str(value.get("content", "")).strip():
            return str(value["content"])
        if str(value.get("ai_msg", "")).strip():
            return str(value["ai_msg"])
        final_result = value.get("final_result")
        if isinstance(final_result, dict):
            for key in ("ai_msg", "answer", "content"):
                if str(final_result.get(key, "")).strip():
                    return str(final_result[key])
        choices = value.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            message = choice.get("message") if isinstance(choice, dict) else {}
            if isinstance(message, dict) and str(message.get("content", "")).strip():
                return str(message["content"])
            if isinstance(choice, dict) and str(choice.get("text", "")).strip():
                return str(choice["text"])
        rounds = value.get("llm_rounds")
        if isinstance(rounds, list):
            for item in reversed(rounds):
                if not isinstance(item, dict):
                    continue
                for key in ("response", "message", "final_result"):
                    text = extract_chat_response_text(item.get(key))
                    if text:
                        return text
    if isinstance(value, str):
        return value
    return ""


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


def _load_json_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def _summary_from_mapping(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("chapter_compressed_summary", "chapter_summary", "summary_payload"):
        nested = value.get(key)
        if isinstance(nested, dict):
            summary = str(nested.get("summary", "")).strip()
            if summary:
                return summary
    summary = str(value.get("summary", "")).strip()
    return summary


def _summary_from_tool_arguments(arguments: Any, *, _depth: int = 0) -> str:
    if _depth > 4:
        return ""
    value = _load_json_value(arguments)
    if isinstance(value, str):
        return ""
    if not isinstance(value, dict):
        return ""

    summary = _summary_from_mapping(value)
    if summary:
        return summary

    for key in ("payload", "raw"):
        nested = value.get(key)
        if nested is None:
            continue
        summary = _summary_from_tool_arguments(nested, _depth=_depth + 1)
        if summary:
            return summary
    return ""


def _summary_from_tool_calls(tool_calls: Any) -> str:
    if not isinstance(tool_calls, list):
        return ""
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if isinstance(function, dict):
            summary = _summary_from_tool_arguments(function.get("arguments"))
            if summary:
                return summary
        summary = _summary_from_tool_arguments(tool_call.get("arguments"))
        if summary:
            return summary
    return ""


def _summary_from_agent_interaction(value: dict[str, Any]) -> str:
    tool_events = value.get("tool_events")
    if isinstance(tool_events, list):
        for event in tool_events:
            if isinstance(event, dict):
                summary = _summary_from_tool_arguments(event.get("arguments"))
                if summary:
                    return summary

    llm_rounds = value.get("llm_rounds")
    if isinstance(llm_rounds, list):
        for round_item in llm_rounds:
            if not isinstance(round_item, dict):
                continue
            response = round_item.get("response")
            if isinstance(response, dict):
                summary = _summary_from_tool_calls(response.get("tool_calls"))
                if summary:
                    return summary

    return _summary_from_tool_calls(value.get("tool_calls"))


def extract_summary_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ""
    if not isinstance(value, dict):
        return ""

    summary = _summary_from_mapping(value)
    if summary:
        return summary

    summary = _summary_from_agent_interaction(value)
    if summary:
        return summary

    choices = value.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return extract_summary_text(_load_json_value(content))
        summary = _summary_from_tool_calls(
            message.get("tool_calls") if isinstance(message, dict) else None
        )
        if summary:
            return summary
    return ""
