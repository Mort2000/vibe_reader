"""Verify agent interaction audit capture for ParagraphCommentAgent."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)

from ..config import Settings
from .agent_base import COMMENT_INSTRUCTIONS, CommentDensityHint
from .verify_telemetry import PROMPT_VERSION

logger = logging.getLogger(__name__)

CURRENT_WINDOW_TAG = "<CURRENT_WINDOW>"

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|authorization|bearer\s+\S+|sk-[a-z0-9]{20,})"),
    re.compile(r"(?i)(cookie|session[_-]?token)\s*[:=]\s*\S+"),
)

_SCHEMA_VERSION = "verify_agent_interaction_v1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


def redact_secrets(value: Any) -> tuple[Any, int]:
    """Recursively redact secret-like strings; return (value, redaction_count)."""
    count = 0

    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            if pattern.search(redacted):
                redacted = pattern.sub("***REDACTED***", redacted)
                count += 1
        return redacted, count

    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            cleaned, n = redact_secrets(item)
            out.append(cleaned)
            count += n
        return out, count

    if isinstance(value, dict):
        out_dict: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"api_key", "authorization", "cookie"}:
                out_dict[key] = "***REDACTED***"
                count += 1
                continue
            cleaned, n = redact_secrets(item)
            out_dict[key] = cleaned
            count += n
        return out_dict, count

    return value, count


def build_original_text_block(
    *,
    component: str,
    paragraphs: list[dict[str, Any]],
    book_id: int | None = None,
    chapter_idx: int | None = None,
    text_mode: str = "range_edge_excerpt",
    edge_paragraph_max_chars: int = 800,
) -> dict[str, Any]:
    if not paragraphs:
        return {
            "type": "original_text_block",
            "component": component,
            "paragraph_range": [0, 0],
            "paragraph_count": 0,
            "char_count": 0,
            "token_estimate": 0,
            "content_hash": sha256_text(""),
            "text_mode": text_mode,
        }

    ordered = sorted(paragraphs, key=lambda p: p["paragraph_idx"])
    start_idx = ordered[0]["paragraph_idx"]
    end_idx = ordered[-1]["paragraph_idx"]
    full_text = "\n".join(p.get("text", "") for p in ordered)
    char_count = sum(int(p.get("char_count") or len(p.get("text", ""))) for p in ordered)
    token_estimate = sum(int(p.get("token_estimate") or 0) for p in ordered) or estimate_tokens(
        full_text
    )

    block: dict[str, Any] = {
        "type": "original_text_block",
        "component": component,
        "book_id": book_id,
        "chapter_idx": chapter_idx,
        "paragraph_range": [start_idx, end_idx],
        "paragraph_count": len(ordered),
        "char_count": char_count,
        "token_estimate": token_estimate,
        "content_hash": sha256_text(full_text),
        "text_mode": text_mode,
    }

    def _paragraph_entry(p: dict[str, Any], *, truncate: bool) -> dict[str, Any]:
        text = p.get("text", "")
        truncated = False
        if truncate and len(text) > edge_paragraph_max_chars:
            text = text[:edge_paragraph_max_chars] + "…"
            truncated = True
        return {
            "paragraph_idx": p["paragraph_idx"],
            "char_count": len(p.get("text", "")),
            "text": text,
            "text_truncated": truncated,
            "hash": sha256_text(p.get("text", "")),
        }

    if text_mode == "full":
        block["paragraphs"] = [
            {
                "paragraph_idx": p["paragraph_idx"],
                "text": p.get("text", ""),
                "hash": sha256_text(p.get("text", "")),
            }
            for p in ordered
        ]
    else:
        first = _paragraph_entry(ordered[0], truncate=True)
        last = _paragraph_entry(ordered[-1], truncate=True)
        block["first_paragraph"] = first
        block["last_paragraph"] = last

    return block


def _split_user_prompt_segments(
    prompt: str,
    window_paragraphs: list[dict[str, Any]],
    *,
    book_id: int,
    chapter_idx: int,
    text_mode: str,
    edge_paragraph_max_chars: int,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []

    if CURRENT_WINDOW_TAG not in prompt:
        logger.warning(
            "agent_audit.prompt_segment_split_failed",
            extra={
                "event": "agent_audit.prompt_segment_split_failed",
                "fields": {
                    "anchor": CURRENT_WINDOW_TAG,
                    "fallback": "prompt_text_plus_window_block",
                },
            },
        )
        if prompt.strip():
            segments.append({"type": "text", "text": prompt.strip()})
        if window_paragraphs:
            segments.append(
                build_original_text_block(
                    component="current_window",
                    paragraphs=window_paragraphs,
                    book_id=book_id,
                    chapter_idx=chapter_idx,
                    text_mode=text_mode,
                    edge_paragraph_max_chars=edge_paragraph_max_chars,
                )
            )
        return segments

    before, _, after = prompt.partition(CURRENT_WINDOW_TAG)
    if before.strip():
        segments.append({"type": "text", "text": before.strip()})

    if window_paragraphs:
        segments.append(
            build_original_text_block(
                component="current_window",
                paragraphs=window_paragraphs,
                book_id=book_id,
                chapter_idx=chapter_idx,
                text_mode=text_mode,
                edge_paragraph_max_chars=edge_paragraph_max_chars,
            )
        )

    if after.strip():
        segments.append({"type": "text", "text": after.strip()})

    return segments


def build_injected_context(
    *,
    window_paragraphs: list[dict[str, Any]],
    target_paragraphs: list[int],
    density_hint: CommentDensityHint | None,
    book_id: int,
    chapter_idx: int,
    text_mode: str,
    edge_paragraph_max_chars: int,
    context_hash: str,
) -> dict[str, Any]:
    components: list[dict[str, Any]] = [
        {
            "name": "system_policy",
            "source": "prompt_template",
            "included": True,
            "token_estimate": estimate_tokens(COMMENT_INSTRUCTIONS),
            "hash": sha256_text(COMMENT_INSTRUCTIONS),
        }
    ]

    window_block = build_original_text_block(
        component="current_window",
        paragraphs=window_paragraphs,
        book_id=book_id,
        chapter_idx=chapter_idx,
        text_mode=text_mode,
        edge_paragraph_max_chars=edge_paragraph_max_chars,
    )
    components.append(
        {
            "name": "current_window",
            "source": "book_paragraphs",
            "included": True,
            "render_action": (
                "range_edge_excerpt_for_markdown"
                if text_mode == "range_edge_excerpt"
                else "full_for_markdown"
            ),
            "token_estimate": window_block.get("token_estimate", 0),
            "hash": window_block.get("content_hash", ""),
            "content": window_block,
        }
    )

    target_meta = {
        "name": "comment_target_paragraphs",
        "source": "runtime_metadata",
        "included": True,
        "token_estimate": max(1, len(target_paragraphs) * 2),
        "hash": sha256_text(json.dumps(sorted(target_paragraphs))),
        "content": {"paragraphs": sorted(target_paragraphs)},
    }
    components.append(target_meta)

    if density_hint is not None:
        density_text = json.dumps(
            {
                "stat_start_paragraph_idx": density_hint.stat_start_paragraph_idx,
                "stat_end_paragraph_idx": density_hint.stat_end_paragraph_idx,
                "stat_target_paragraph_count": density_hint.stat_target_paragraph_count,
                "active_comment_count": density_hint.active_comment_count,
                "soft_min_density": density_hint.soft_min_density,
                "current_density": density_hint.current_density,
                "estimated_missing_comments": density_hint.estimated_missing_comments,
            },
            ensure_ascii=False,
        )
        components.append(
            {
                "name": "comment_density_hint",
                "source": "runtime_metadata",
                "included": True,
                "token_estimate": estimate_tokens(density_text),
                "hash": sha256_text(density_text),
                "content": json.loads(density_text),
            }
        )

    total_estimate = sum(int(c.get("token_estimate") or 0) for c in components)
    return {
        "builder": "ContextBuilder",
        "builder_version": "context_builder_v1",
        "total_input_token_estimate": total_estimate,
        "hard_input_cap": None,
        "context_hash": context_hash,
        "components": components,
    }


def _extract_thinking(response: ModelResponse) -> dict[str, Any]:
    parts: list[str] = []
    for part in response.parts:
        if isinstance(part, ThinkingPart):
            parts.append(part.content)
    if parts:
        text = "\n".join(parts)
        cleaned, redactions = redact_secrets(text)
        return {
            "available": True,
            "field": "thinking",
            "text": cleaned,
            "secret_redaction_count": redactions,
        }
    return {"available": False, "reason": "adapter_not_exposed"}


def _round_usage(
    *,
    round_idx: int,
    usage_source: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
) -> dict[str, Any]:
    if round_idx == 0:
        return {
            "source": usage_source,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
        }
    return {
        "source": usage_source,
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "note": "per_round_usage_not_available_from_adapter",
    }


def extract_tool_calls_from_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Extract provider tool call IDs and arguments from PydanticAI messages."""
    calls: list[dict[str, Any]] = []
    round_idx = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            args = part.args if isinstance(part.args, dict) else {"raw": part.args}
            payload = args.get("payload") if isinstance(args.get("payload"), dict) else args
            calls.append(
                {
                    "tool_call_id": part.tool_call_id,
                    "round_idx": round_idx,
                    "tool_name": part.tool_name,
                    "arguments": {"payload": payload},
                    "payload": payload,
                }
            )
        round_idx += 1
    return calls


def extract_llm_rounds(
    messages: list[Any],
    *,
    model: str,
    duration_ms: float,
    usage_source: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []
    round_idx = 0
    pending_request: ModelRequest | None = None

    for message in messages:
        if isinstance(message, ModelRequest):
            pending_request = message
            continue
        if not isinstance(message, ModelResponse):
            continue

        tool_calls: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                tool_calls.append(
                    {
                        "id": part.tool_call_id,
                        "name": part.tool_name,
                        "arguments": part.args if isinstance(part.args, dict) else {"raw": part.args},
                    }
                )
            elif isinstance(part, TextPart):
                text_parts.append(part.content)

        req_usage_input = input_tokens if round_idx == 0 else None
        req_usage_output = output_tokens if round_idx == 0 else None
        req_cached = cached_input_tokens if round_idx == 0 else None

        usage = _round_usage(
            round_idx=round_idx,
            usage_source=usage_source,
            input_tokens=req_usage_input,
            output_tokens=req_usage_output,
            cached_input_tokens=req_cached,
        )
        timing: dict[str, Any] = {
            "latency_ms": duration_ms if round_idx == 0 else None,
            "retry_index": 0,
        }
        if round_idx > 0 and timing["latency_ms"] is None:
            timing["note"] = "per_round_latency_not_available_from_adapter"

        rounds.append(
            {
                "round_idx": round_idx,
                "request": {
                    "provider": "openai_compatible",
                    "model": model,
                    "stream": False,
                    "tool_choice": "auto" if tool_calls or round_idx == 0 else None,
                    "tools": [{"name": "emit_comment"}] if round_idx == 0 else [],
                },
                "response": {
                    "status": "ok",
                    "content": "\n".join(text_parts),
                    "thinking": _extract_thinking(message),
                    "tool_calls": tool_calls,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                    "usage": usage,
                },
                "timing": timing,
            }
        )
        round_idx += 1
        pending_request = None

    if not rounds and pending_request is not None:
        rounds.append(
            {
                "round_idx": 0,
                "request": {"provider": "openai_compatible", "model": model, "stream": False},
                "response": {
                    "status": "ok",
                    "content": "",
                    "thinking": {"available": False, "reason": "adapter_not_exposed"},
                    "tool_calls": [],
                    "finish_reason": "unknown",
                    "usage": {
                        "source": usage_source,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cached_input_tokens": cached_input_tokens,
                    },
                },
                "timing": {"latency_ms": duration_ms, "retry_index": 0},
            }
        )

    return rounds


def build_tool_events(
    *,
    tool_calls: list[dict[str, Any]],
    valid_comments: list[dict[str, Any]],
    discarded: list[dict[str, Any]],
    validation_failed_count: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    valid_by_para = {c["paragraph_idx"]: c for c in valid_comments}

    for tool_call in tool_calls:
        payload = tool_call.get("payload") or {}
        para_idx = payload.get("paragraph_idx")
        tool_call_id = tool_call["tool_call_id"]
        round_idx = tool_call.get("round_idx", 0)
        discarded_match = next(
            (d for d in discarded if d.get("payload") == payload),
            None,
        )
        if discarded_match:
            reason = discarded_match.get("reason", "discarded")
            business_status = "discarded"
            persistence = {"status": "not_inserted"}
            business = {"status": business_status, "reason": reason, "target_paragraph": False}
            schema_status = "passed" if reason != "validation_failed" else "failed"
        else:
            business_status = "passed"
            persistence = {"status": "inserted", "comment_id": None}
            business = {"status": business_status, "target_paragraph": True, "duplicate": False}
            schema_status = "passed"
            if para_idx in valid_by_para:
                persistence["comment_id"] = valid_by_para[para_idx].get("comment_id")

        events.append(
            {
                "tool_call_id": tool_call_id,
                "round_idx": round_idx,
                "tool_name": tool_call.get("tool_name") or "emit_comment",
                "arguments": tool_call.get("arguments") or {"payload": payload},
                "tool_result": {"status": "ok", "content": "accepted"},
                "schema_validation": {"status": schema_status},
                "business_validation": business,
                "persistence": persistence,
                "created_at": _now(),
            }
        )

    if validation_failed_count and not tool_calls:
        events.append(
            {
                "tool_call_id": "call_emit_comment_none",
                "round_idx": 0,
                "tool_name": "emit_comment",
                "arguments": {},
                "schema_validation": {"status": "failed"},
                "business_validation": {"status": "discarded", "reason": "validation_failed"},
                "persistence": {"status": "not_inserted"},
                "created_at": _now(),
            }
        )

    return events


def build_comment_interaction_packet(
    *,
    invocation_id: str,
    trace_id: str,
    verify_run_id: str,
    verify_scenario_id: str,
    verify_step_id: str,
    job_id: int,
    book: dict[str, Any],
    chapter_idx: int,
    window: dict[str, Any],
    window_paragraphs: list[dict[str, Any]],
    target_paragraphs: list[int],
    density_hint: CommentDensityHint | None,
    prompt: str,
    agent_result: Any,
    settings: Settings,
    duration_ms: float,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
    raw_payloads: list[dict[str, Any]],
    valid_comments: list[dict[str, Any]],
    discarded: list[dict[str, Any]],
    validation_failed_count: int,
    no_call: bool,
    usage_source: str = "estimate",
    text_mode: str = "range_edge_excerpt",
    edge_paragraph_max_chars: int = 800,
) -> dict[str, Any]:
    context_hash = window.get("context_hash") or sha256_text(prompt)
    user_segments = _split_user_prompt_segments(
        prompt,
        window_paragraphs,
        book_id=book["id"],
        chapter_idx=chapter_idx,
        text_mode=text_mode,
        edge_paragraph_max_chars=edge_paragraph_max_chars,
    )
    prompt_messages = [
        {
            "role": "system",
            "content": [{"type": "text", "text": COMMENT_INSTRUCTIONS}],
        },
        {"role": "user", "content": user_segments},
    ]

    injected_context = build_injected_context(
        window_paragraphs=window_paragraphs,
        target_paragraphs=target_paragraphs,
        density_hint=density_hint,
        book_id=book["id"],
        chapter_idx=chapter_idx,
        text_mode=text_mode,
        edge_paragraph_max_chars=edge_paragraph_max_chars,
        context_hash=context_hash,
    )

    messages = agent_result.all_messages() if agent_result is not None else []
    llm_rounds = extract_llm_rounds(
        messages,
        model=settings.llm.model,
        duration_ms=duration_ms,
        usage_source=usage_source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )

    tool_calls = extract_tool_calls_from_messages(messages)
    tool_events = build_tool_events(
        tool_calls=tool_calls,
        valid_comments=valid_comments,
        discarded=discarded,
        validation_failed_count=validation_failed_count,
    )

    final_result: dict[str, Any] = {
        "status": "completed",
        "comments_created": [
            {
                "comment_id": c.get("comment_id"),
                "paragraph_idx": c["paragraph_idx"],
                "comment_type": c.get("comment_type"),
                "text": c.get("comment"),
            }
            for c in valid_comments
        ],
        "comments_discarded": [
            {
                "paragraph_idx": d.get("payload", {}).get("paragraph_idx"),
                "reason": d.get("reason"),
            }
            for d in discarded
        ],
        "no_call": no_call,
    }

    packet: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "run_id": verify_run_id,
        "scenario_id": verify_scenario_id,
        "step_id": verify_step_id,
        "agent": "ParagraphCommentAgent",
        "llm_mode": None,
        "stub_profile": None,
        "model": settings.llm.model,
        "book": {
            "id": book.get("id"),
            "title": book.get("title"),
            "corpus_sha256": book.get("corpus_sha256"),
        },
        "chapter_idx": chapter_idx,
        "window": {
            "id": window.get("id"),
            "seq": window.get("window_seq", window.get("seq")),
            "start_paragraph_idx": window.get("start_paragraph_idx"),
            "end_paragraph_idx": window.get("end_paragraph_idx"),
            "focus_start_paragraph_idx": window.get("focus_start_paragraph_idx"),
            "focus_end_paragraph_idx": window.get("focus_end_paragraph_idx"),
        },
        "prompt_version": PROMPT_VERSION,
        "context_hash": context_hash,
        "trace_id": trace_id,
        "job_id": job_id,
        "prompt_messages": prompt_messages,
        "injected_context": injected_context,
        "llm_rounds": llm_rounds,
        "tool_events": tool_events,
        "validation_events": [],
        "final_result": final_result,
        "usage": {
            "source": usage_source,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
        },
        "timing": {
            "total_ms": duration_ms,
            "ttft_ms": None,
            "retry_count": 0,
        },
        "markdown_report_path": f"audit/agent_reports/{invocation_id}.md",
        "content_rendering": {
            "markdown_original_text_mode": text_mode,
            "secret_redaction_count": 0,
            "body_redaction_required": False,
        },
        "created_at": _now(),
    }

    cleaned, redactions = redact_secrets(packet)
    cleaned["content_rendering"]["secret_redaction_count"] = redactions
    return cleaned


def make_invocation_id(agent: str, scenario_id: str, job_id: int) -> str:
    """Build a per-verify-run invocation id.

    Uses monotonic ``job_id`` as the sequence suffix. Verify runs are one-shot,
    so this is sufficient for in-run correlation; cross-run replay after job
    table reset may reuse suffixes.
    """
    short = scenario_id.split("_")[0] if scenario_id else "unknown"
    agent_slug = {
        "ParagraphCommentAgent": "comment",
        "ReadingChatAgent": "chat",
        "ContextCompactionAgent": "compaction",
    }.get(agent, agent.lower())
    return f"inv_{agent_slug}_{short}_{job_id:04d}"
