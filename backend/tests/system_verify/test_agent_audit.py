"""Unit tests for agent audit report generation."""

from __future__ import annotations

from pathlib import Path

from .agent_audit_exporter import (
    AgentAuditExporter,
    assert_agent_audit_artifacts,
    enrich_comment_records_with_agent_refs,
    normalize_audit_packet,
)
from .agent_audit_report import render_agent_audit_markdown, render_reading_position
from .core.config import VerifyConfig
from .core.run_manager import RunManager


def _sample_packet() -> dict:
    return {
        "schema_version": "verify_agent_interaction_v1",
        "invocation_id": "inv_comment_S2_0001",
        "run_id": "20260523T120000Z_abcd1234",
        "scenario_id": "S2_continuous_reading",
        "step_id": "wait_for_comments",
        "agent": "ParagraphCommentAgent",
        "llm_mode": "stub",
        "stub_profile": "mvp_default",
        "model": "deepseek-v4-flash",
        "book": {"id": 1, "title": "Test Book"},
        "chapter_idx": 1,
        "window": {
            "id": 7,
            "seq": 2,
            "start_paragraph_idx": 10,
            "end_paragraph_idx": 20,
            "focus_start_paragraph_idx": 15,
            "focus_end_paragraph_idx": 20,
        },
        "prompt_version": "paragraph_comment_v1",
        "context_hash": "sha256:abc123",
        "trace_id": "trace_test_001",
        "prompt_messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": "system instructions"}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "comment_target_paragraphs = [15]"},
                    {
                        "type": "original_text_block",
                        "component": "current_window",
                        "paragraph_range": [10, 20],
                        "paragraph_count": 11,
                        "char_count": 1200,
                        "token_estimate": 400,
                        "content_hash": "sha256:windowhash",
                        "text_mode": "range_edge_excerpt",
                        "first_paragraph": {
                            "paragraph_idx": 10,
                            "text": "第一段内容",
                            "text_truncated": False,
                        },
                        "last_paragraph": {
                            "paragraph_idx": 20,
                            "text": "最后一段内容",
                            "text_truncated": False,
                        },
                    },
                ],
            },
        ],
        "injected_context": {
            "builder": "ContextBuilder",
            "builder_version": "context_builder_v1",
            "total_input_token_estimate": 900,
            "context_hash": "sha256:abc123",
            "components": [
                {
                    "name": "current_window",
                    "source": "book_paragraphs",
                    "included": True,
                    "token_estimate": 400,
                }
            ],
        },
        "llm_rounds": [
            {
                "round_idx": 0,
                "request": {"model": "deepseek-v4-flash", "stream": False},
                "response": {
                    "status": "ok",
                    "content": "",
                    "thinking": {"available": False, "reason": "adapter_not_exposed"},
                    "tool_calls": [
                        {
                            "id": "call_emit_comment_15",
                            "name": "emit_comment",
                            "arguments": {
                                "payload": {
                                    "paragraph_idx": 15,
                                    "comment": "语气收紧",
                                    "comment_type": "observation",
                                }
                            },
                        }
                    ],
                    "finish_reason": "tool_calls",
                    "usage": {
                        "source": "estimate",
                        "input_tokens": 1200,
                        "output_tokens": 80,
                        "cached_input_tokens": 0,
                    },
                },
                "timing": {"latency_ms": 842.0, "retry_index": 0},
            }
        ],
        "tool_events": [
            {
                "tool_call_id": "call_emit_comment_15",
                "tool_name": "emit_comment",
                "schema_validation": {"status": "passed"},
                "business_validation": {"status": "passed"},
                "persistence": {"status": "inserted", "comment_id": 42},
            }
        ],
        "final_result": {
            "status": "completed",
            "comments_created": [
                {
                    "comment_id": 42,
                    "paragraph_idx": 15,
                    "comment_type": "observation",
                    "text": "语气收紧",
                }
            ],
            "comments_discarded": [],
            "no_call": False,
        },
        "usage": {
            "source": "estimate",
            "input_tokens": 1200,
            "output_tokens": 80,
            "cached_input_tokens": 0,
        },
        "timing": {"total_ms": 842.0, "ttft_ms": None, "retry_count": 0},
        "content_rendering": {
            "markdown_original_text_mode": "range_edge_excerpt",
            "secret_redaction_count": 0,
            "body_redaction_required": False,
        },
        "created_at": "2026-05-23T12:00:00Z",
    }


def test_render_agent_audit_markdown_includes_usage_and_prompt(tmp_path: Path) -> None:
    markdown = render_agent_audit_markdown(_sample_packet())
    assert "## Usage / Timing" in markdown
    assert "1200" in markdown
    assert "## Prompt" in markdown
    assert "第一段内容" in markdown
    assert "## Tool Calls" in markdown
    assert "inv_comment_S2_0001" in markdown


def test_agent_audit_exporter_writes_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = VerifyConfig()
    run_manager = RunManager(config, run_id="20260523T120000Z_test0001")
    run_manager.start()

    exporter = AgentAuditExporter(run_manager, config)
    counts = exporter.export_from_agent_runs(
        [
            {
                "trace_id": "trace_test_001",
                "invocation_id": "inv_comment_S2_0001",
                "interaction": _sample_packet(),
            }
        ]
    )

    assert counts["agent_reports"] == 1
    assert (run_manager.base_dir / "audit" / "agent_invocations.ndjson").exists()
    assert (
        run_manager.base_dir / "audit" / "agent_reports" / "inv_comment_S2_0001.md"
    ).exists()
    assert (
        run_manager.base_dir
        / "audit"
        / "agent_interactions"
        / "inv_comment_S2_0001.json"
    ).exists()

    failures = assert_agent_audit_artifacts(run_manager.base_dir)
    assert failures == []


def test_enrich_comment_records_with_agent_refs(tmp_path: Path) -> None:
    comments_path = tmp_path / "comments.ndjson"
    comments_path.write_text(
        '{"sample_id":"comment_S2_0001","trace_id":"trace_test_001"}\n',
        encoding="utf-8",
    )
    changed = enrich_comment_records_with_agent_refs(
        comments_path,
        {"trace_test_001": "inv_comment_S2_0001"},
    )
    assert changed == 1
    row = comments_path.read_text(encoding="utf-8")
    assert "agent_invocation_id" in row
    assert "agent_report_path" in row


def test_normalize_audit_packet_maps_legacy_compaction_fields() -> None:
    normalized = normalize_audit_packet(
        {
            "agent": "ContextCompactionAgent",
            "verify_run_id": "run_1",
            "verify_scenario_id": "R1_real_happy_path",
            "verify_step_id": "advance_for_compaction",
            "book_id": 1,
            "llm_rounds": [{"request": {"model": "deepseek-v4-flash"}}],
        }
    )
    assert normalized["run_id"] == "run_1"
    assert normalized["scenario_id"] == "R1_real_happy_path"
    assert normalized["step_id"] == "advance_for_compaction"
    assert normalized["book"] == {"id": 1}
    assert normalized["model"] == "deepseek-v4-flash"
    assert normalized["prompt_version"] == "chapter_compaction_v1"


def test_render_reading_position_uses_source_chunk_for_compaction() -> None:
    lines = render_reading_position(
        {
            "book": {"id": 1, "title": "Test Book"},
            "chapter_idx": 1,
            "source_chunk": {
                "id": 2,
                "chunk_seq": 0,
                "start_paragraph_idx": 0,
                "end_paragraph_idx": 179,
            },
        }
    )
    text = "\n".join(lines)
    assert "Test Book" in text
    assert "Source chunk: P0-P179" in text
    assert "Window:" not in text


def test_render_reading_position_prefers_window_when_both_present() -> None:
    lines = render_reading_position(
        {
            "book": {"id": 1, "title": "Test Book"},
            "chapter_idx": 1,
            "source_chunk": {"id": 2, "start_paragraph_idx": 0, "end_paragraph_idx": 179},
            "window": {
                "id": 5,
                "seq": 4,
                "start_paragraph_idx": 436,
                "end_paragraph_idx": 521,
                "focus_start_paragraph_idx": 440,
                "focus_end_paragraph_idx": 521,
            },
        }
    )
    text = "\n".join(lines)
    assert "Window: P436-P521" in text
    assert "Source chunk:" not in text


def test_render_compaction_audit_markdown_includes_metadata() -> None:
    markdown = render_agent_audit_markdown(
        normalize_audit_packet(
            {
                "invocation_id": "inv_compaction_R1_0005",
                "agent": "ContextCompactionAgent",
                "verify_run_id": "run_1",
                "verify_scenario_id": "R1_real_happy_path",
                "verify_step_id": "advance_for_compaction",
                "trace_id": "trace_x",
                "llm_mode": "real",
                "book_id": 1,
                "book": {"id": 1, "title": "Test Book"},
                "chapter_idx": 1,
                "model": "deepseek-v4-flash",
                "prompt_version": "chapter_compaction_v1",
                "source_chunk": {
                    "id": 2,
                    "chunk_seq": 0,
                    "start_paragraph_idx": 0,
                    "end_paragraph_idx": 179,
                },
                "prompt_messages": [],
                "usage": {"source": "provider", "input_tokens": 100, "output_tokens": 10},
            }
        )
    )
    assert "| Scenario | R1_real_happy_path |" in markdown
    assert "| Model | deepseek-v4-flash |" in markdown
    assert "Test Book" in markdown
    assert "Source chunk: P0-P179" in markdown
