"""Tests for agent audit capture helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.domain.models import ChapterCompressedSummary, OriginalTextChunk
from app.verification.audit_packets import (
    CURRENT_WINDOW_TAG,
    _round_usage,
    _split_user_prompt_segments,
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


def test_split_user_prompt_segments_warns_without_anchor(caplog) -> None:
    segments = _split_user_prompt_segments(
        "plain prompt without anchor",
        [{"paragraph_idx": 1, "text": "hello", "char_count": 5}],
        book_id=1,
        chapter_idx=1,
        text_mode="range_edge_excerpt",
        edge_paragraph_max_chars=800,
    )
    assert len(segments) == 2
    assert segments[0]["type"] == "text"
    assert segments[1]["type"] == "original_text_block"
    assert CURRENT_WINDOW_TAG not in "plain prompt without anchor"
    assert any("prompt_segment_split_failed" in r.message for r in caplog.records)


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
