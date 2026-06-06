from __future__ import annotations

from typing import Any, Protocol

import aiosqlite

from ..application.agent_run_result import (
    AgentRunResult,
    ChatAuditContext,
    CommentAuditContext,
    CompactionAuditContext,
)


class AuditSink(Protocol):
    async def persist_agent_run(
        self,
        db: aiosqlite.Connection,
        *,
        result: AgentRunResult,
        settings: Any,
        trace_id: str,
        job_id: int,
        book_id: int,
        chapter_idx: int,
        window_id: int | None,
    ) -> None: ...


def _audit_config(settings: Any) -> Any:
    observability = getattr(settings, "observability", None)
    return getattr(observability, "audit", None)


def _redacted(reason: str) -> dict[str, Any]:
    return {"redacted": True, "reason": reason}


def _redact_prompt_messages(packet: dict[str, Any]) -> None:
    messages = packet.get("prompt_messages")
    if not isinstance(messages, list):
        return
    packet["prompt_messages"] = [
        {
            "role": message.get("role") if isinstance(message, dict) else "",
            "content": _redacted("full prompt disabled by observability.audit"),
        }
        for message in messages
    ]


def _summarize_component(component: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "name",
        "source",
        "tokens",
        "token_estimate",
        "included",
        "paragraph_range",
        "paragraph_count",
        "char_count",
        "content_hash",
        "text_mode",
    }
    summary = {key: component[key] for key in allowed_keys if key in component}
    content = component.get("content")
    if isinstance(content, dict):
        content_keys = {
            "id",
            "chunk_id",
            "chunk_seq",
            "summary_id",
            "compaction_epoch",
            "start_paragraph_idx",
            "end_paragraph_idx",
            "covered_start_paragraph_idx",
            "covered_end_paragraph_idx",
            "token_estimate",
            "hash",
            "content_hash",
            "text_hash",
            "turn_count",
        }
        safe_content = {key: content[key] for key in content_keys if key in content}
        if safe_content:
            summary["content"] = safe_content
    return summary


def _redact_injected_context(packet: dict[str, Any]) -> None:
    context = packet.get("injected_context")
    if not isinstance(context, dict):
        return
    redacted: dict[str, Any] = {
        "redacted": True,
        "reason": "full prompt disabled by observability.audit",
    }
    for key in (
        "builder",
        "builder_version",
        "context_hash",
        "total_input_token_estimate",
    ):
        if key in context:
            redacted[key] = context[key]
    components = context.get("components")
    if isinstance(components, list):
        redacted["components"] = [
            _summarize_component(component)
            for component in components
            if isinstance(component, dict)
        ]
    packet["injected_context"] = redacted


def _redact_llm_rounds(packet: dict[str, Any]) -> None:
    rounds = packet.get("llm_rounds")
    if not isinstance(rounds, list):
        return
    for item in rounds:
        if not isinstance(item, dict):
            continue
        response = item.get("response")
        if not isinstance(response, dict):
            continue
        if "content" in response:
            response["content"] = ""
            response["content_redacted"] = True
        if "thinking" in response:
            response["thinking"] = _redacted(
                "model output disabled by observability.audit"
            )
        tool_calls = response.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if isinstance(call, dict) and "arguments" in call:
                    call["arguments"] = _redacted(
                        "model output disabled by observability.audit"
                    )


def _redact_tool_events(packet: dict[str, Any]) -> None:
    events = packet.get("tool_events")
    if not isinstance(events, list):
        return
    for event in events:
        if not isinstance(event, dict):
            continue
        if "arguments" in event:
            event["arguments"] = _redacted(
                "model output disabled by observability.audit"
            )
        tool_result = event.get("tool_result")
        if isinstance(tool_result, dict) and "content" in tool_result:
            tool_result["content"] = ""
            tool_result["content_redacted"] = True


def _redact_final_result(packet: dict[str, Any]) -> None:
    result = packet.get("final_result")
    if not isinstance(result, dict):
        return
    if isinstance(result.get("comments_created"), list):
        for comment in result["comments_created"]:
            if isinstance(comment, dict) and "text" in comment:
                comment["text"] = ""
                comment["text_redacted"] = True
    for key in ("summary", "ai_msg", "user_msg"):
        if key in result:
            result[key] = ""
            result[f"{key}_redacted"] = True
    if "anchor_excerpts" in result:
        result["anchor_excerpts"] = []
        result["anchor_excerpts_redacted"] = True
    if "user_msg" in packet:
        packet["user_msg"] = ""
        packet["user_msg_redacted"] = True


def _apply_audit_config(packet: dict[str, Any], settings: Any) -> dict[str, Any]:
    audit = _audit_config(settings)
    if audit is None:
        return packet

    if not getattr(audit, "include_prompt_manifest", True):
        packet["prompt_manifest"] = _redacted(
            "prompt manifest disabled by observability.audit"
        )

    if not getattr(audit, "include_full_prompt", False):
        _redact_prompt_messages(packet)
        _redact_injected_context(packet)

    if not getattr(audit, "include_model_response", False):
        _redact_llm_rounds(packet)
        _redact_tool_events(packet)
        _redact_final_result(packet)

    if getattr(audit, "redact_secrets", True):
        from ..verification.audit_packets import redact_secrets

        packet, _ = redact_secrets(packet)

    return packet


class DefaultAuditSink:
    async def persist_agent_run(
        self,
        db: aiosqlite.Connection,
        *,
        result: AgentRunResult,
        settings: Any,
        trace_id: str,
        job_id: int,
        book_id: int,
        chapter_idx: int,
        window_id: int | None,
    ) -> None:
        from ..observability import (
            get_verify_run_id,
            get_verify_scenario_id,
            get_verify_step_id,
        )
        from ..verification.audit_packets import (
            build_comment_interaction_packet,
            build_compaction_interaction_packet,
            make_invocation_id,
        )
        from ..services.agent_audit_store import persist_interaction_packet
        from ..services.verify_telemetry import persist_agent_run

        interaction_path = ""
        audit_ctx = result.audit_context

        if audit_ctx is not None:
            verify_run_id = get_verify_run_id()
            verify_scenario_id = get_verify_scenario_id()
            verify_step_id = get_verify_step_id()
            invocation_id = make_invocation_id(
                result.agent_name, verify_scenario_id, job_id
            )
            result.invocation_id = invocation_id

            if isinstance(audit_ctx, CommentAuditContext):
                packet = build_comment_interaction_packet(
                    invocation_id=invocation_id,
                    trace_id=audit_ctx.trace_id,
                    verify_run_id=verify_run_id,
                    verify_scenario_id=verify_scenario_id,
                    verify_step_id=verify_step_id,
                    job_id=job_id,
                    book=audit_ctx.book,
                    chapter_idx=audit_ctx.chapter_idx,
                    window=audit_ctx.window,
                    window_paragraphs=audit_ctx.window_paragraphs,
                    target_paragraphs=audit_ctx.target_paragraphs,
                    density_hint=audit_ctx.density_hint,
                    prompt=audit_ctx.prompt,
                    agent_result=audit_ctx.agent_result,
                    settings=settings,
                    duration_ms=result.duration_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cached_input_tokens=result.cached_input_tokens,
                    raw_payloads=audit_ctx.raw_payloads,
                    valid_comments=audit_ctx.valid_comments,
                    discarded=audit_ctx.discarded,
                    validation_failed_count=audit_ctx.validation_failed_count,
                    no_call=audit_ctx.no_call,
                    usage_source=audit_ctx.usage_source,
                    context_manifest=audit_ctx.context_manifest,
                )
                packet = _apply_audit_config(packet, settings)
                interaction_path = persist_interaction_packet(
                    settings.data_dir,
                    verify_run_id=verify_run_id,
                    invocation_id=invocation_id,
                    packet=packet,
                )
            elif isinstance(audit_ctx, CompactionAuditContext):
                from ..repos import books as book_repo

                book = await book_repo.get_book(db, book_id) or {
                    "id": book_id
                }
                packet = build_compaction_interaction_packet(
                    invocation_id=invocation_id,
                    trace_id=audit_ctx.trace_id,
                    verify_run_id=verify_run_id,
                    verify_scenario_id=verify_scenario_id,
                    verify_step_id=verify_step_id,
                    job_id=job_id,
                    book_id=book_id,
                    book=book,
                    chapter_idx=chapter_idx,
                    source_chunk=audit_ctx.source_chunk,
                    previous_summary_row=audit_ctx.previous_summary_row,
                    next_summary_row=audit_ctx.next_summary_row,
                    prompt=audit_ctx.prompt,
                    agent_result=audit_ctx.agent_result,
                    settings=settings,
                    duration_ms=result.duration_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cached_input_tokens=result.cached_input_tokens,
                    transaction_committed=audit_ctx.transaction_committed,
                    prompt_manifest=audit_ctx.prompt_manifest,
                )
                packet = _apply_audit_config(packet, settings)
                interaction_path = persist_interaction_packet(
                    settings.data_dir,
                    verify_run_id=verify_run_id,
                    invocation_id=invocation_id,
                    packet=packet,
                )
            elif isinstance(audit_ctx, ChatAuditContext):
                from ..repos import books as book_repo
                from ..verification.audit_packets import build_chat_interaction_packet

                book = await book_repo.get_book(db, book_id) or {
                    "id": book_id
                }
                packet = build_chat_interaction_packet(
                    invocation_id=invocation_id,
                    trace_id=audit_ctx.trace_id,
                    verify_run_id=verify_run_id,
                    verify_scenario_id=verify_scenario_id,
                    verify_step_id=verify_step_id,
                    book=book,
                    chapter_idx=chapter_idx,
                    paragraph_idx=audit_ctx.paragraph_idx,
                    prompt=audit_ctx.prompt,
                    agent_result=audit_ctx.agent_result,
                    settings=settings,
                    duration_ms=result.duration_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    recent_chat_turns=audit_ctx.recent_chat_turns,
                    user_msg=audit_ctx.user_msg,
                    prompt_manifest=audit_ctx.prompt_manifest or result.prompt_manifest,
                )
                packet = _apply_audit_config(packet, settings)
                interaction_path = persist_interaction_packet(
                    settings.data_dir,
                    verify_run_id=verify_run_id,
                    invocation_id=invocation_id,
                    packet=packet,
                )

        payload = result.to_telemetry_dict(interaction_path=interaction_path)
        await persist_agent_run(
            db,
            trace_id=trace_id,
            job_id=job_id,
            book_id=book_id,
            chapter_idx=chapter_idx,
            window_id=window_id,
            payload=payload,
        )
