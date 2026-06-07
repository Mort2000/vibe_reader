"""Verify agent interaction audit packet construction.

Moved from ``services/agent_audit`` during R3 so that the audit persistence
layer (``infrastructure/audit``) imports from the verification package instead
of depending on the services layer.
"""

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
from ..domain.models import ChapterCompressedSummary, OriginalTextChunk, ReadingWindow
from ..services.agent_base import COMMENT_INSTRUCTIONS, CommentDensityHint
from ..services.verify_telemetry import COMPACTION_PROMPT_VERSION, PROMPT_VERSION

logger = logging.getLogger(__name__)

CURRENT_WINDOW_TAG = "<CURRENT_WINDOW>"

_MANIFEST_COMPONENT_SOURCES = {
    "system_policy": "prompt_template",
    "metadata": "runtime_metadata",
    "reserved": "token_budget",
    "chapter_compressed_summary": "context_builder",
    "previous_chapter_summary": "context_builder",
    "source_original_chunk": "book_paragraphs",
    "live_original_chunks": "book_paragraphs",
    "ephemeral_recent_comments": "comment_history",
    "ephemeral_recent_chat": "chat_history",
    "current_task": "runtime_metadata",
}

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|authorization|bearer\s+\S+|sk-[a-z0-9]{20,})"),
    re.compile(r"(?i)(cookie|session[_-]?token)\s*[:=]\s*\S+"),
)

_SCHEMA_VERSION = "verify_agent_interaction_v1"


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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


def _effective_model_name(settings: Any, agent: str) -> str:
    if hasattr(settings, "effective_llm"):
        return settings.effective_llm(agent).model
    return getattr(getattr(settings, "llm", None), "model", "")


def _component_tokens(component: dict[str, Any]) -> int:
    return int(component.get("token_estimate") or component.get("tokens") or 0)


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
    char_count = sum(
        int(p.get("char_count") or len(p.get("text", ""))) for p in ordered
    )
    token_estimate = sum(
        int(p.get("token_estimate") or 0) for p in ordered
    ) or estimate_tokens(full_text)

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
        if prompt.strip():
            segments.append({"type": "text", "text": prompt.strip()})
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


def _extract_tagged_prompt_block(prompt: str, tag: str) -> str | None:
    start = f"<{tag}>"
    end = f"</{tag}>"
    begin = prompt.find(start)
    if begin < 0:
        return None
    finish = prompt.find(end, begin + len(start))
    if finish < 0:
        return None
    text = prompt[begin + len(start) : finish].strip()
    return text or None


def _manifest_component_content(
    name: str,
    component: dict[str, Any],
    manifest: dict[str, Any],
    *,
    prompt: str,
    recent_chat_turns: list[dict[str, Any]] | None,
) -> Any:
    if component.get("content") is not None:
        return component.get("content")

    if name == "chapter_compressed_summary":
        content: dict[str, Any] = {}
        if manifest.get("summary_id") is not None:
            content["summary_id"] = manifest.get("summary_id")
        summary_text = _extract_tagged_prompt_block(
            prompt, "CHAPTER_COMPRESSED_SUMMARY"
        )
        if summary_text:
            content["summary"] = summary_text
        return content or None

    if name == "live_original_chunks":
        return {
            "live_chunk_ids": manifest.get("live_chunk_ids") or [],
            "live_start_paragraph_idx": manifest.get("live_start_paragraph_idx"),
            "frontier_paragraph_idx": manifest.get("frontier_paragraph_idx"),
            "partial_chunk_id": manifest.get("partial_chunk_id"),
            "partial_frontier_paragraph_idx": manifest.get(
                "partial_frontier_paragraph_idx"
            ),
        }

    if name == "ephemeral_recent_chat" and recent_chat_turns is not None:
        return {
            "turns": recent_chat_turns,
            "turn_count": len(recent_chat_turns),
        }

    return None


def build_injected_context_from_prompt_manifest(
    manifest: dict[str, Any] | None,
    *,
    prompt: str = "",
    context_hash: str = "",
    recent_chat_turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build verify sidecar context from the actual ContextBuilder manifest."""
    if not manifest:
        return None

    components: list[dict[str, Any]] = []
    for entry in manifest.get("components") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        if not name:
            continue
        normalized: dict[str, Any] = {
            "name": name,
            "source": entry.get("source")
            or _MANIFEST_COMPONENT_SOURCES.get(name, "context_builder"),
            "included": bool(entry.get("included", True)),
            "token_estimate": _component_tokens(entry),
        }
        for key in ("hash", "render_action", "drop_reason"):
            if entry.get(key) is not None:
                normalized[key] = entry[key]

        content = _manifest_component_content(
            name,
            entry,
            manifest,
            prompt=prompt,
            recent_chat_turns=recent_chat_turns,
        )
        if content is not None:
            normalized["content"] = content
        components.append(normalized)

    total_estimate = manifest.get("total_estimate")
    if total_estimate is None:
        total_estimate = manifest.get("safe_total_estimate")
    if total_estimate is None:
        total_estimate = sum(_component_tokens(c) for c in components)

    return {
        "builder": manifest.get("builder", "ContextBuilder"),
        "builder_version": manifest.get("builder_version", "context_builder_v1"),
        "total_input_token_estimate": int(total_estimate or 0),
        "raw_total_estimate": manifest.get("raw_total_estimate"),
        "safe_total_estimate": manifest.get("safe_total_estimate"),
        "hard_input_cap": manifest.get("hard_cap"),
        "attention_target": manifest.get("attention_target"),
        "context_hash": context_hash or manifest.get("context_hash", ""),
        "components": components,
        "live_chunk_ids": manifest.get("live_chunk_ids") or [],
        "summary_id": manifest.get("summary_id"),
        "compaction_epoch": manifest.get("compaction_epoch"),
        "context_degraded": bool(manifest.get("context_degraded", False)),
        "preflight_triggered": bool(manifest.get("preflight_triggered", False)),
        "hard_triggered": bool(manifest.get("hard_triggered", False)),
        "token_estimator": manifest.get("token_estimator"),
    }


def enrich_injected_context_from_build_manifest(
    injected_context: dict[str, Any],
    manifest: dict[str, Any] | None,
    *,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Attach chapter summary / compaction metadata from ContextBuilder manifest."""
    manifest_context = build_injected_context_from_prompt_manifest(
        manifest,
        prompt=prompt or "",
        context_hash=injected_context.get("context_hash") or "",
    )
    if not manifest_context:
        return injected_context

    manifest_names = {
        str(c.get("name") or "") for c in manifest_context.get("components") or []
    }
    extra_components = [
        c
        for c in injected_context.get("components") or []
        if str(c.get("name") or "") not in manifest_names
    ]
    if extra_components:
        manifest_context = {
            **manifest_context,
            "components": manifest_context["components"] + extra_components,
        }
    return manifest_context


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
            payload = (
                args.get("payload") if isinstance(args.get("payload"), dict) else args
            )
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


def _assistant_text_from_llm_rounds(llm_rounds: list[dict[str, Any]]) -> str:
    for round_item in reversed(llm_rounds):
        content = (round_item.get("response") or {}).get("content") or ""
        if str(content).strip():
            return str(content)
    return ""


def extract_llm_rounds(
    messages: list[Any],
    *,
    model: str,
    duration_ms: float,
    usage_source: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
    default_request_tools: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []
    round_idx = 0
    pending_request: ModelRequest | None = None
    if default_request_tools is None:
        default_request_tools = [{"name": "emit_comment"}]

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
                        "arguments": part.args
                        if isinstance(part.args, dict)
                        else {"raw": part.args},
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
                    "tools": default_request_tools if round_idx == 0 else [],
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

    if len(rounds) > 1 and input_tokens is not None:
        usage = (rounds[0].get("response") or {}).get("usage") or {}
        usage["scope"] = "run_aggregate"
        usage["note"] = (
            "provider usage is aggregated for the full tool-calling run; "
            "per-round usage is not available from the adapter"
        )

    if not rounds and pending_request is not None:
        rounds.append(
            {
                "round_idx": 0,
                "request": {
                    "provider": "openai_compatible",
                    "model": model,
                    "stream": False,
                },
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
            persistence = {"status": "notinserted"}
            business = {
                "status": business_status,
                "reason": reason,
                "target_paragraph": False,
            }
            schema_status = "passed" if reason != "validation_failed" else "failed"
        else:
            business_status = "passed"
            persistence = {"status": "inserted", "comment_id": None}
            business = {
                "status": business_status,
                "target_paragraph": True,
                "duplicate": False,
            }
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
                "business_validation": {
                    "status": "discarded",
                    "reason": "validation_failed",
                },
                "persistence": {"status": "notinserted"},
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
    window: ReadingWindow,
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
    context_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context_hash = window.context_hash or sha256_text(prompt)
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

    injected_context = build_injected_context_from_prompt_manifest(
        context_manifest,
        prompt=prompt,
        context_hash=context_hash,
    )
    if injected_context is None:
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
    model_name = _effective_model_name(settings, "comment")
    llm_rounds = extract_llm_rounds(
        messages,
        model=model_name,
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
        "model": model_name,
        "book": {
            "id": book.get("id"),
            "title": book.get("title"),
            "corpus_sha256": book.get("corpus_sha256"),
        },
        "chapter_idx": chapter_idx,
        "window": {
            "id": window.id,
            "seq": window.window_seq,
            "start_paragraph_idx": window.start_paragraph_idx,
            "end_paragraph_idx": window.end_paragraph_idx,
            "focus_start_paragraph_idx": window.focus_start_paragraph_idx,
            "focus_end_paragraph_idx": window.focus_end_paragraph_idx,
        },
        "prompt_version": PROMPT_VERSION,
        "context_hash": context_hash,
        "trace_id": trace_id,
        "job_id": job_id,
        "prompt_messages": prompt_messages,
        "prompt_manifest": context_manifest or {},
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


def build_compaction_interaction_packet(
    *,
    invocation_id: str,
    trace_id: str,
    verify_run_id: str,
    verify_scenario_id: str,
    verify_step_id: str,
    job_id: int,
    book_id: int,
    book: dict[str, Any],
    chapter_idx: int,
    source_chunk: OriginalTextChunk,
    previous_summary_row: ChapterCompressedSummary | None,
    next_summary_row: ChapterCompressedSummary,
    prompt: str,
    agent_result: Any,
    settings: Any,
    duration_ms: float,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
    transaction_committed: bool,
    prompt_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    usage_source = "provider" if input_tokens is not None else "estimate"
    model_name = _effective_model_name(settings, "compaction")

    llm_rounds = extract_llm_rounds(
        agent_result.all_messages(),
        model=model_name,
        duration_ms=duration_ms,
        usage_source=usage_source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )

    from ..services.agent_base import COMPACTION_INSTRUCTIONS

    if prompt_manifest is None:
        system_tokens = estimate_tokens(COMPACTION_INSTRUCTIONS)
        previous_tokens = (
            int(previous_summary_row.token_estimate or 0)
            if previous_summary_row
            else 0
        )
        prompt_manifest = {
            "builder": "CompactionPromptBuilder",
            "builder_version": "compaction_prompt_v1",
            "total_estimate": system_tokens
            + previous_tokens
            + int(source_chunk.token_estimate or 0),
            "context_hash": sha256_text(prompt),
            "components": [
                {
                    "name": "system_policy",
                    "tokens": system_tokens,
                    "source": "prompt_template",
                },
                {
                    "name": "previous_chapter_summary",
                    "tokens": previous_tokens,
                    "source": "context_builder",
                    "included": previous_summary_row is not None,
                    "content": (
                        {
                            "summary_id": previous_summary_row.id,
                            "covered_start_paragraph_idx": previous_summary_row.covered_start_paragraph_idx,
                            "covered_end_paragraph_idx": previous_summary_row.covered_end_paragraph_idx,
                            "compaction_epoch": previous_summary_row.compaction_epoch,
                        }
                        if previous_summary_row
                        else None
                    ),
                },
                {
                    "name": "source_original_chunk",
                    "tokens": int(source_chunk.token_estimate or 0),
                    "source": "book_paragraphs",
                    "content": {
                        "chunk_id": source_chunk.id,
                        "chunk_seq": source_chunk.chunk_seq,
                        "start_paragraph_idx": source_chunk.start_paragraph_idx,
                        "end_paragraph_idx": source_chunk.end_paragraph_idx,
                        "token_estimate": source_chunk.token_estimate,
                    },
                },
            ],
            "summary_id": next_summary_row.id,
            "compaction_epoch": next_summary_row.compaction_epoch,
        }

    prompt_messages = [
        {
            "role": "system",
            "content": [{"type": "text", "text": COMPACTION_INSTRUCTIONS}],
        },
        {"role": "user", "content": [{"type": "text", "text": prompt}]},
    ]

    messages = agent_result.all_messages()
    tool_calls = extract_tool_calls_from_messages(messages)
    tool_events: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        tool_events.append(
            {
                "tool_call_id": tool_call["tool_call_id"],
                "round_idx": tool_call.get("round_idx", 0),
                "tool_name": tool_call.get("tool_name")
                or "emit_chapter_compressed_summary",
                "arguments": tool_call.get("arguments") or {},
                "tool_result": {"status": "ok", "content": "accepted"},
                "schema_validation": {"status": "passed"},
                "business_validation": {"status": "passed"},
                "persistence": {
                    "status": "inserted",
                    "summary_id": next_summary_row.id,
                },
                "created_at": _now(),
            }
        )

    context_hash = sha256_text(prompt)
    injected_context = build_injected_context_from_prompt_manifest(
        prompt_manifest,
        prompt=prompt,
        context_hash=context_hash,
    )
    if injected_context is None:
        injected_context = {
            "components": [
                {
                    "name": "source_original_chunk",
                    "content": {
                        "chunk_id": source_chunk.id,
                        "start_paragraph_idx": source_chunk.start_paragraph_idx,
                        "end_paragraph_idx": source_chunk.end_paragraph_idx,
                        "token_estimate": source_chunk.token_estimate,
                    },
                },
                {
                    "name": "chapter_compressed_summary",
                    "content": {
                        "id": next_summary_row.id,
                        "covered_start_paragraph_idx": next_summary_row.covered_start_paragraph_idx,
                        "covered_end_paragraph_idx": next_summary_row.covered_end_paragraph_idx,
                        "token_estimate": next_summary_row.token_estimate,
                        "compaction_epoch": next_summary_row.compaction_epoch,
                    },
                },
            ],
            "total_input_token_estimate": input_tokens
            or source_chunk.token_estimate,
        }
    book_payload = {
        "id": book.get("id", book_id),
        "title": book.get("title"),
        "corpus_sha256": book.get("corpus_sha256") or book.get("file_hash"),
    }

    packet: dict[str, Any] = {
        "schema_version": "compaction_v1",
        "invocation_id": invocation_id,
        "run_id": verify_run_id,
        "scenario_id": verify_scenario_id,
        "step_id": verify_step_id,
        "agent": "ContextCompactionAgent",
        "llm_mode": None,
        "stub_profile": None,
        "model": model_name,
        "book": book_payload,
        "chapter_idx": chapter_idx,
        "prompt_version": COMPACTION_PROMPT_VERSION,
        "context_hash": context_hash,
        "trace_id": trace_id,
        "job_id": job_id,
        "book_id": book_id,
        "source_chunk": {
            "id": source_chunk.id,
            "chunk_seq": source_chunk.chunk_seq,
            "start_paragraph_idx": source_chunk.start_paragraph_idx,
            "end_paragraph_idx": source_chunk.end_paragraph_idx,
            "text_hash": source_chunk.text_hash,
            "token_estimate": source_chunk.token_estimate,
        },
        "previous_summary": (
            {
                "id": previous_summary_row.id,
                "covered_start": previous_summary_row.covered_start_paragraph_idx,
                "covered_end": previous_summary_row.covered_end_paragraph_idx,
                "token_estimate": previous_summary_row.token_estimate,
                "compaction_epoch": previous_summary_row.compaction_epoch,
            }
            if previous_summary_row
            else None
        ),
        "next_summary": {
            "id": next_summary_row.id,
            "covered_start": next_summary_row.covered_start_paragraph_idx,
            "covered_end": next_summary_row.covered_end_paragraph_idx,
            "token_estimate": next_summary_row.token_estimate,
            "compaction_epoch": next_summary_row.compaction_epoch,
        },
        "prompt_manifest": prompt_manifest or {},
        "prompt_messages": prompt_messages,
        "injected_context": injected_context,
        "llm_rounds": llm_rounds,
        "tool_events": tool_events,
        "final_result": {
            "status": "completed" if transaction_committed else "not_committed",
            "summary_id": next_summary_row.id,
            "summary": next_summary_row.summary,
            "anchor_excerpts": next_summary_row.anchor_excerpts,
            "covered_start_paragraph_idx": next_summary_row.covered_start_paragraph_idx,
            "covered_end_paragraph_idx": next_summary_row.covered_end_paragraph_idx,
            "compaction_epoch": next_summary_row.compaction_epoch,
        },
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
        "duration_ms": duration_ms,
        "transaction_committed": transaction_committed,
        "markdown_report_path": f"audit/agent_reports/{invocation_id}.md",
        "created_at": _now(),
    }

    cleaned, redactions = redact_secrets(packet)
    return cleaned


def build_chat_interaction_packet(
    *,
    invocation_id: str,
    trace_id: str,
    verify_run_id: str,
    verify_scenario_id: str,
    verify_step_id: str,
    book: dict[str, Any],
    chapter_idx: int,
    paragraph_idx: int,
    prompt: str,
    agent_result: Any,
    settings: Settings,
    duration_ms: float,
    input_tokens: int | None,
    output_tokens: int | None,
    recent_chat_turns: list[dict[str, Any]],
    user_msg: str = "",
    prompt_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    book_payload = {"id": book.get("id"), "title": book.get("title")}

    prompt_messages = [{"role": "user", "content": prompt}]
    messages = agent_result.all_messages()
    usage_source = "provider" if input_tokens is not None else "estimate"
    model_name = _effective_model_name(settings, "chat")
    llm_rounds = extract_llm_rounds(
        messages,
        model=model_name,
        duration_ms=duration_ms,
        usage_source=usage_source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=None,
        default_request_tools=[],
    )
    tool_events: list[dict[str, Any]] = []
    ai_msg = _assistant_text_from_llm_rounds(llm_rounds)
    context_hash = (prompt_manifest or {}).get("context_hash", "")
    if context_hash and not str(context_hash).startswith("sha256:"):
        context_hash = f"sha256:{context_hash}"

    chat_turns_payload = []
    for t in recent_chat_turns:
        chat_turns_payload.append({
            "user_msg": t.get("user_msg", ""),
            "ai_msg": t.get("ai_msg") or "",
            "status": t.get("status", ""),
        })

    components = [dict(c) for c in (prompt_manifest or {}).get("components", [])]
    for comp in components:
        if comp.get("name") == "ephemeral_recent_chat":
            comp["content"] = {
                "turns": chat_turns_payload,
                "turn_count": len(chat_turns_payload),
            }
            break

    normalized_manifest = {**(prompt_manifest or {}), "components": components}
    injected_context = build_injected_context_from_prompt_manifest(
        normalized_manifest,
        prompt=prompt,
        context_hash=(prompt_manifest or {}).get("context_hash", ""),
        recent_chat_turns=chat_turns_payload,
    ) or {
        "builder": "ContextBuilder",
        "builder_version": "context_builder_v1",
        "total_input_token_estimate": sum(_component_tokens(c) for c in components),
        "context_hash": (prompt_manifest or {}).get("context_hash", ""),
        "components": components,
    }

    packet: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "agent": "ReadingChatAgent",
        "agent_name": "ReadingChatAgent",
        "run_id": verify_run_id,
        "scenario_id": verify_scenario_id,
        "step_id": verify_step_id,
        "verify_run_id": verify_run_id,
        "verify_scenario_id": verify_scenario_id,
        "verify_step_id": verify_step_id,
        "book": book_payload,
        "chapter_idx": chapter_idx,
        "paragraph_idx": paragraph_idx,
        "user_msg": user_msg,
        "trace_id": trace_id,
        "prompt_version": "chat_v1",
        "context_hash": context_hash,
        "prompt_manifest": prompt_manifest or {},
        "prompt_messages": prompt_messages,
        "injected_context": injected_context,
        "llm_rounds": llm_rounds,
        "tool_events": tool_events,
        "final_result": {
            "status": "completed" if ai_msg else "empty",
            "user_msg": user_msg,
            "ai_msg": ai_msg,
        },
        "usage": {
            "source": usage_source,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "timing": {"total_ms": duration_ms},
        "duration_ms": duration_ms,
        "markdown_report_path": f"audit/agent_reports/{invocation_id}.md",
        "created_at": _now(),
    }

    cleaned, _ = redact_secrets(packet)
    return cleaned
