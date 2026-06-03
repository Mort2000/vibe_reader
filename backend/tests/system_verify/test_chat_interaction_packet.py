"""Unit tests for ReadingChatAgent verify interaction packets (R1 A4)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.verification.audit_packets import build_chat_interaction_packet
from tests.system_verify.assertions.context import assert_chapter_summary_in_subsequent_context
from tests.system_verify.core.scenario import StepAssertionError


def _post_compaction_prompt() -> str:
    return "\n".join(
        [
            "<CHAPTER_COMPRESSED_SUMMARY>",
            "主角在第一章经历政变与逃亡，留下开放悬念。",
            "</CHAPTER_COMPRESSED_SUMMARY>",
            "<LIVE_ORIGINAL_CHUNKS start_p=400 end_p=521>",
            "[p=521] 当前段落正文。",
            "</LIVE_ORIGINAL_CHUNKS>",
            "<CURRENT_TASK>",
            "current_reading_paragraph_idx = 521",
            "mode = chat",
            "</CURRENT_TASK>",
            "",
            "用户提问：压缩之后，前面章节的主要情节是什么？",
        ]
    )


def _context_builder_manifest() -> dict:
    """Minimal manifest as returned by ContextBuilder.build_context."""
    return {
        "summary_id": 9,
        "compaction_epoch": 1,
        "context_hash": "42e814fc0b15861d",
        "components": [
            {"name": "system_policy", "tokens": 3000},
            {"name": "metadata", "tokens": 800},
            {"name": "chapter_compressed_summary", "tokens": 4200},
            {"name": "live_original_chunks", "tokens": 2000},
            {"name": "ephemeral_recent_comments", "tokens": 0},
            {"name": "ephemeral_recent_chat", "tokens": 0},
            {"name": "current_task", "tokens": 50},
        ],
    }


def _build_packet(*, settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    agent_result = MagicMock()
    agent_result.all_messages.return_value = []
    return build_chat_interaction_packet(
        invocation_id="inv_chat_R1_0000",
        trace_id="trace_chat_r1",
        verify_run_id="20260531T120000Z_test",
        verify_scenario_id="R1_real_happy_path",
        verify_step_id="post_compaction_chat",
        book={"id": 1, "title": "Test Book"},
        chapter_idx=1,
        paragraph_idx=521,
        prompt=_post_compaction_prompt(),
        agent_result=agent_result,
        settings=settings,
        duration_ms=40.0,
        input_tokens=7396,
        output_tokens=8,
        recent_chat_turns=[],
        user_msg="压缩之后，前面章节的主要情节是什么？",
        prompt_manifest=_context_builder_manifest(),
    )


def test_build_chat_packet_includes_agent_final_result_and_canonical_fields() -> None:
    packet = _build_packet()
    assert packet["agent"] == "ReadingChatAgent"
    assert packet["schema_version"] == "verify_agent_interaction_v1"
    assert packet["run_id"] == "20260531T120000Z_test"
    assert packet["prompt_version"] == "chat_v1"
    assert packet["context_hash"] == "sha256:42e814fc0b15861d"
    assert packet["final_result"]["status"] == "empty"
    assert packet["final_result"]["user_msg"] == "压缩之后，前面章节的主要情节是什么？"
    assert packet["final_result"]["ai_msg"] == ""


def test_build_chat_packet_injected_context_passes_r1_a4_summary_assertion() -> None:
    """R1 A4 post_compaction_chat checks chapter summary via injected_context."""
    packet = _build_packet()
    compaction_run = {
        "interaction": {
            "final_result": {
                "summary_id": 9,
                "summary": "主角在第一章经历政变与逃亡，留下开放悬念。",
                "anchor_excerpts": [],
            }
        }
    }
    assert_chapter_summary_in_subsequent_context(
        packet["injected_context"],
        compaction_run=compaction_run,
    )


def test_build_chat_packet_normalizes_manifest_tokens_without_duplicate_summary() -> None:
    packet = _build_packet()
    components = packet["injected_context"]["components"]
    summary_components = [
        c for c in components if c.get("name") == "chapter_compressed_summary"
    ]
    assert len(summary_components) == 1
    assert summary_components[0]["token_estimate"] == 4200
    assert packet["injected_context"]["total_input_token_estimate"] == 10050


def test_raw_manifest_only_tokens_fails_summary_assertion() -> None:
    """Documents the pre-fix gap: manifest uses ``tokens``, not verify ``content``."""
    manifest = _context_builder_manifest()
    injected_context = {
        "builder": "ContextBuilder",
        "components": list(manifest["components"]),
    }
    with pytest.raises(StepAssertionError) as exc_info:
        assert_chapter_summary_in_subsequent_context(
            injected_context,
            compaction_run={
                "interaction": {"final_result": {"summary_id": 9}},
            },
        )
    assert "content or token estimate" in exc_info.value.message
