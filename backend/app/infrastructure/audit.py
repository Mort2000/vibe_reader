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
