"""Tests for agent audit capture helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai.messages import ModelResponse, TextPart

from app.config import ModelConfig, ModelDefaultsConfig, Settings
from app.domain.models import ChapterCompressedSummary, OriginalTextChunk, ReadingWindow
from app.verification.audit_packets import (
    CURRENT_WINDOW_TAG,
    _round_usage,
    _split_user_prompt_segments,
    build_comment_interaction_packet,
    build_chat_interaction_packet,
    build_compaction_interaction_packet,
    build_tool_events,
)
from app.services.agent_audit_store import (
    load_interaction_packet,
    persist_interaction_packet,
)


def test_round_usage_marks_follow_up_rounds() -> None:
    usage = _round_usage(
        round_idx=1,
        usage_source="estimate",
        input_tokens=100,
        output_tokens=20,
        cached_input_tokens=0,
    )
    assert usage["input_tokens"] is None
    assert usage["note"] == "per_round_usage_not_available_from_adapter"


def test_split_user_prompt_segments_without_anchor_keeps_actual_prompt_only() -> None:
    segments = _split_user_prompt_segments(
        "plain prompt without anchor",
        [{"paragraph_idx": 1, "text": "hello", "char_count": 5}],
        book_id=1,
        chapter_idx=1,
        text_mode="range_edge_excerpt",
        edge_paragraph_max_chars=800,
    )
    assert len(segments) == 1
    assert segments[0]["type"] == "text"
    assert CURRENT_WINDOW_TAG not in "plain prompt without anchor"


def test_build_comment_packet_uses_prompt_manifest_not_current_window() -> None:
    prompt = "\n".join(
        [
            "<LIVE_ORIGINAL_CHUNKS>",
            "<CHUNK seq=1 start_p=180 end_p=359>",
            "[p=180] live text",
            "</CHUNK>",
            "</LIVE_ORIGINAL_CHUNKS>",
            "<CURRENT_TASK>",
            "comment_target_paragraphs = [180..=180]",
            "</CURRENT_TASK>",
        ]
    )
    manifest = {
        "context_hash": "ctx123",
        "total_estimate": 19_000,
        "live_chunk_ids": [10],
        "components": [
            {"name": "system_policy", "tokens": 3000},
            {"name": "reserved", "tokens": 12000},
            {"name": "live_original_chunks", "tokens": 4000},
            {"name": "current_task", "tokens": 30},
        ],
    }

    packet = build_comment_interaction_packet(
        invocation_id="inv_comment_R1_0002",
        trace_id="trace_x",
        verify_run_id="run_1",
        verify_scenario_id="R1_real_happy_path",
        verify_step_id="advance_for_comments",
        job_id=2,
        book={"id": 1, "title": "Test Book"},
        chapter_idx=1,
        window=ReadingWindow(
            id=7,
            book_id=1,
            chapter_idx=1,
            window_seq=2,
            start_paragraph_idx=197,
            end_paragraph_idx=320,
            focus_start_paragraph_idx=197,
            focus_end_paragraph_idx=320,
            assistant_frontier_paragraph_idx=320,
        ),
        window_paragraphs=[
            {"paragraph_idx": 197, "text": "window edge", "char_count": 11}
        ],
        target_paragraphs=[180],
        density_hint=None,
        prompt=prompt,
        agent_result=None,
        settings=Settings(),
        duration_ms=1.0,
        input_tokens=100,
        output_tokens=5,
        cached_input_tokens=None,
        raw_payloads=[],
        valid_comments=[],
        discarded=[],
        validation_failed_count=0,
        no_call=True,
        usage_source="provider",
        context_manifest=manifest,
    )

    component_names = [
        c["name"] for c in packet["injected_context"]["components"]
    ]
    assert "live_original_chunks" in component_names
    assert "current_window" not in component_names
    assert packet["injected_context"]["total_input_token_estimate"] == 19_000
    user_content = packet["prompt_messages"][1]["content"]
    assert len(user_content) == 1
    assert "LIVE_ORIGINAL_CHUNKS" in user_content[0]["text"]


def test_build_tool_events_uses_provider_tool_call_ids() -> None:
    tool_calls = [
        {
            "tool_call_id": "real_call_abc",
            "round_idx": 0,
            "tool_name": "emit_comment",
            "arguments": {
                "payload": {
                    "paragraph_idx": 15,
                    "comment": "test",
                    "comment_type": "observation",
                }
            },
            "payload": {
                "paragraph_idx": 15,
                "comment": "test",
                "comment_type": "observation",
            },
        }
    ]
    events = build_tool_events(
        tool_calls=tool_calls,
        valid_comments=[
            {
                "paragraph_idx": 15,
                "comment": "test",
                "comment_type": "observation",
                "comment_id": 42,
            }
        ],
        discarded=[],
        validation_failed_count=0,
    )
    assert len(events) == 1
    assert events[0]["tool_call_id"] == "real_call_abc"


def test_persist_and_load_interaction_packet(tmp_path: Path) -> None:
    packet = {"invocation_id": "inv_comment_S2_0001", "trace_id": "trace_x"}
    rel = persist_interaction_packet(
        tmp_path,
        verify_run_id="run_1",
        invocation_id="inv_comment_S2_0001",
        packet=packet,
    )
    loaded = load_interaction_packet(tmp_path, rel)
    assert loaded == packet
    assert json.loads((tmp_path / rel).read_text(encoding="utf-8")) == packet


def test_build_compaction_interaction_packet_includes_report_metadata() -> None:
    settings = SimpleNamespace(llm=SimpleNamespace(model="deepseek-v4-flash"))

    class _Result:
        @staticmethod
        def all_messages():
            return []

    packet = build_compaction_interaction_packet(
        invocation_id="inv_compaction_R1_0001",
        trace_id="trace_x",
        verify_run_id="run_1",
        verify_scenario_id="R1_real_happy_path",
        verify_step_id="advance_for_compaction",
        job_id=5,
        book_id=1,
        book={"id": 1, "title": "Test Book", "file_hash": "abc"},
        chapter_idx=1,
        source_chunk=OriginalTextChunk.from_row(
            {
                "id": 2,
                "book_id": 1,
                "chapter_idx": 1,
                "chunk_seq": 0,
                "start_paragraph_idx": 0,
                "end_paragraph_idx": 179,
                "token_estimate": 7106,
                "text_hash": "deadbeef",
            }
        ),
        previous_summary_row=None,
        next_summary_row=ChapterCompressedSummary.from_row(
            {
                "id": 1,
                "book_id": 1,
                "chapter_idx": 1,
                "covered_start_paragraph_idx": 0,
                "covered_end_paragraph_idx": 179,
                "token_estimate": 331,
                "compaction_epoch": 1,
            }
        ),
        prompt="compaction prompt",
        agent_result=_Result(),
        settings=settings,
        duration_ms=100.0,
        input_tokens=8235,
        output_tokens=446,
        cached_input_tokens=3840,
        transaction_committed=True,
    )
    assert packet["scenario_id"] == "R1_real_happy_path"
    assert packet["step_id"] == "advance_for_compaction"
    assert packet["model"] == "deepseek-v4-flash"
    assert packet["book"]["title"] == "Test Book"
    assert packet["prompt_version"] == "chapter_compaction_v1"
    assert "verify_scenario_id" not in packet
    assert packet["prompt_manifest"]["builder"] == "CompactionPromptBuilder"
    assert {
        c["name"] for c in packet["prompt_manifest"]["components"]
    } >= {"system_policy", "source_original_chunk"}
    assert packet["injected_context"]["total_input_token_estimate"] > 0
    assert packet["final_result"]["summary_id"] == 1


def test_interaction_packets_use_agent_scoped_effective_models() -> None:
    settings = Settings(
        models=[
            ModelConfig(id="global", model_name="global-model"),
            ModelConfig(id="chat", model_name="chat-model"),
            ModelConfig(id="comment", model_name="comment-model"),
        ],
        defaults=ModelDefaultsConfig(
            global_model_id="global",
            chat_model_id="chat",
            comment_model_id="comment",
        ),
    )

    class _Result:
        @staticmethod
        def all_messages():
            return [ModelResponse(parts=[TextPart(content="ok")])]

    window = ReadingWindow(
        id=7,
        book_id=1,
        chapter_idx=1,
        window_seq=2,
        start_paragraph_idx=0,
        end_paragraph_idx=5,
        focus_start_paragraph_idx=0,
        focus_end_paragraph_idx=5,
        assistant_frontier_paragraph_idx=5,
    )
    comment_packet = build_comment_interaction_packet(
        invocation_id="inv_comment_R1_0001",
        trace_id="trace_x",
        verify_run_id="run_1",
        verify_scenario_id="R1_real_happy_path",
        verify_step_id="advance_for_comments",
        job_id=2,
        book={"id": 1, "title": "Test Book"},
        chapter_idx=1,
        window=window,
        window_paragraphs=[{"paragraph_idx": 0, "text": "text", "char_count": 4}],
        target_paragraphs=[0],
        density_hint=None,
        prompt="comment prompt",
        agent_result=_Result(),
        settings=settings,
        duration_ms=1.0,
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=None,
        raw_payloads=[],
        valid_comments=[],
        discarded=[],
        validation_failed_count=0,
        no_call=False,
    )

    compaction_packet = build_compaction_interaction_packet(
        invocation_id="inv_compaction_R1_0001",
        trace_id="trace_x",
        verify_run_id="run_1",
        verify_scenario_id="R1_real_happy_path",
        verify_step_id="advance_for_compaction",
        job_id=5,
        book_id=1,
        book={"id": 1, "title": "Test Book", "file_hash": "abc"},
        chapter_idx=1,
        source_chunk=OriginalTextChunk.from_row(
            {
                "id": 2,
                "book_id": 1,
                "chapter_idx": 1,
                "chunk_seq": 0,
                "start_paragraph_idx": 0,
                "end_paragraph_idx": 179,
                "token_estimate": 7106,
                "text_hash": "deadbeef",
            }
        ),
        previous_summary_row=None,
        next_summary_row=ChapterCompressedSummary.from_row(
            {
                "id": 1,
                "book_id": 1,
                "chapter_idx": 1,
                "covered_start_paragraph_idx": 0,
                "covered_end_paragraph_idx": 179,
                "token_estimate": 331,
                "compaction_epoch": 1,
            }
        ),
        prompt="compaction prompt",
        agent_result=_Result(),
        settings=settings,
        duration_ms=100.0,
        input_tokens=8235,
        output_tokens=446,
        cached_input_tokens=3840,
        transaction_committed=True,
    )

    chat_packet = build_chat_interaction_packet(
        invocation_id="inv_chat_R1_0001",
        trace_id="trace_x",
        verify_run_id="run_1",
        verify_scenario_id="R1_real_happy_path",
        verify_step_id="chat",
        book={"id": 1, "title": "Test Book"},
        chapter_idx=1,
        paragraph_idx=0,
        prompt="chat prompt",
        agent_result=_Result(),
        settings=settings,
        duration_ms=10.0,
        input_tokens=12,
        output_tokens=3,
        recent_chat_turns=[],
        user_msg="hello",
    )

    assert comment_packet["model"] == "comment-model"
    assert comment_packet["llm_rounds"][0]["request"]["model"] == "comment-model"
    assert compaction_packet["model"] == "comment-model"
    assert compaction_packet["llm_rounds"][0]["request"]["model"] == "comment-model"
    assert chat_packet["llm_rounds"][0]["request"]["model"] == "chat-model"
