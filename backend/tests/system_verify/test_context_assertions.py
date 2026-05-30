"""Unit tests for context/compaction assertion helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.system_verify.core.config import ContextConfig, VerifyConfig
from tests.system_verify.assertions.context import (
    assert_chapter_summary_in_subsequent_context,
    assert_chapter_summary_structure,
    assert_comment_activity_observable,
    assert_compaction_completed,
    assert_compaction_failure_does_not_block_comments,
    assert_compaction_source_scale,
    assert_l2_chunk_boundaries_stable,
    assert_reclaimed_l2_chunk_present,
    assert_token_budget,
    extract_chapter_summary,
    extract_l2_chunks,
    find_comment_agent_runs,
    find_compaction_agent_runs,
    record_context_metrics_from_verify,
    select_post_compaction_comment_runs,
)
from tests.system_verify.flows.reading import ReadingTrace
from tests.system_verify.core.scenario import StepAssertionError


def test_extract_l2_chunks_from_component_content() -> None:
    injected = {
        "components": [
            {
                "name": "live_l2_original_text",
                "content": {
                    "chunks": [
                        {
                            "chunk_id": 1,
                            "start_paragraph_idx": 0,
                            "end_paragraph_idx": 120,
                            "status": "active",
                        }
                    ]
                },
            }
        ]
    }
    chunks = extract_l2_chunks(injected)
    assert len(chunks) == 1
    assert chunks[0]["start_paragraph_idx"] == 0


def test_l2_chunk_boundaries_stable_ignores_reclaimed() -> None:
    before = [
        {
            "chunk_id": 1,
            "start_paragraph_idx": 0,
            "end_paragraph_idx": 120,
            "status": "active",
        },
        {
            "chunk_id": 2,
            "start_paragraph_idx": 121,
            "end_paragraph_idx": 240,
            "status": "reclaimed",
        },
    ]
    after = [
        {
            "chunk_id": 1,
            "start_paragraph_idx": 0,
            "end_paragraph_idx": 120,
            "status": "active",
        },
        {
            "chunk_id": 3,
            "start_paragraph_idx": 241,
            "end_paragraph_idx": 360,
            "status": "active",
        },
    ]
    assert_l2_chunk_boundaries_stable(before, after)


def test_assert_chapter_summary_rejects_forbidden_fields() -> None:
    with pytest.raises(StepAssertionError):
        assert_chapter_summary_structure(
            {
                "summary": "chapter facts",
                "anchor_excerpts": [],
                "comment_digest": "must not persist",
            }
        )


def test_extract_chapter_summary_from_final_result() -> None:
    payload = {
        "final_result": {
            "summary": "compressed chapter",
            "anchor_excerpts": [{"paragraph_idx": 10, "text": "anchor"}],
            "covered_end_paragraph_idx": 240,
        }
    }
    summary = extract_chapter_summary(payload)
    assert summary is not None
    assert summary["summary"] == "compressed chapter"


def test_extract_chapter_summary_from_tool_events_raw_payload() -> None:
    payload = {
        "next_summary": {
            "id": 1,
            "covered_start": 0,
            "covered_end": 179,
            "compaction_epoch": 1,
        },
        "tool_events": [
            {
                "tool_name": "emit_chapter_compressed_summary",
                "arguments": {
                    "payload": {
                        "raw": (
                            '{"payload": {"summary": "章节摘要", '
                            '"anchor_excerpts": ["锚点"], '
                            '"covered_start_paragraph_idx": 0, '
                            '"covered_end_paragraph_idx": 179}}'
                        )
                    }
                },
            }
        ],
    }
    summary = extract_chapter_summary(payload)
    assert summary is not None
    assert summary["summary"] == "章节摘要"
    assert summary["anchor_excerpts"] == ["锚点"]
    assert summary["id"] == 1
    assert summary["covered_end_paragraph_idx"] == 179


def test_assert_token_budget_uses_emergency_cap() -> None:
    config = VerifyConfig(context=ContextConfig(emergency_input_cap_tokens=160_000))
    assert_token_budget({"total_input_token_estimate": 120_000}, config)

    with pytest.raises(StepAssertionError):
        assert_token_budget({"total_input_token_estimate": 180_000}, config)


def test_find_compaction_agent_runs() -> None:
    runs = [
        {"agent": "ParagraphCommentAgent"},
        {
            "agent_name": "ContextCompactionAgent",
            "interaction": {"agent": "ContextCompactionAgent"},
        },
    ]
    compaction = find_compaction_agent_runs(runs)
    assert len(compaction) == 1


def test_find_comment_agent_runs() -> None:
    runs = [
        {"agent": "ParagraphCommentAgent"},
        {"agent_name": "ContextCompactionAgent"},
    ]
    comments = find_comment_agent_runs(runs)
    assert len(comments) == 1
    assert comments[0]["agent"] == "ParagraphCommentAgent"


def test_assert_compaction_source_scale_normal_path() -> None:
    assert_compaction_source_scale(
        {
            "interaction": {
                "compaction_source": {
                    "token_estimate": 18000,
                    "paragraph_count": 130,
                }
            }
        },
        min_source_tokens=16000,
        min_source_paragraphs=120,
    )


def test_assert_compaction_source_scale_raises_when_unavailable() -> None:
    with pytest.raises(StepAssertionError) as exc:
        assert_compaction_source_scale(
            {"interaction": {"agent": "ContextCompactionAgent"}},
            min_source_tokens=16000,
            min_source_paragraphs=120,
        )
    assert exc.value.assertion == "compaction_source_scale_unavailable"


def test_assert_compaction_completed_requires_job_or_run() -> None:
    with pytest.raises(StepAssertionError) as exc:
        assert_compaction_completed(compaction_jobs=[], compaction_runs=[])
    assert exc.value.assertion == "compaction_completed"


def test_assert_compaction_completed_requires_agent_run() -> None:
    with pytest.raises(StepAssertionError) as exc:
        assert_compaction_completed(
            compaction_jobs=[{"status": "done", "job_type": "compact_context"}],
            compaction_runs=[],
            require_agent_run=True,
        )
    assert exc.value.assertion == "compaction_agent_run"


def test_assert_reclaimed_l2_chunk_present_from_context() -> None:
    assert_reclaimed_l2_chunk_present(
        injected_contexts=[
            {
                "components": [
                    {
                        "name": "live_l2_original_text",
                        "content": {
                            "chunks": [
                                {
                                    "chunk_id": 1,
                                    "status": "reclaimed",
                                }
                            ]
                        },
                    }
                ]
            }
        ]
    )


def test_assert_reclaimed_l2_chunk_present_missing() -> None:
    with pytest.raises(StepAssertionError) as exc:
        assert_reclaimed_l2_chunk_present(
            injected_contexts=[{"components": []}],
            compaction_jobs=[{"status": "done"}],
        )
    assert exc.value.assertion == "reclaimed_l2_chunk_present"


def test_record_context_metrics_from_verify() -> None:
    metrics = MagicMock()
    record_context_metrics_from_verify(
        metrics,
        {
            "latency": {"context.build_ms": {"p50": 120.0, "max": 300.0}},
            "context": {
                "input_token_estimate": 95000,
                "l2_chunk_count": 3,
                "compaction_epoch": 1,
            },
            "tokens": {
                "ContextCompactionAgent": {"input": 18000, "output": 900},
            },
        },
        scenario_id="S4_long_context",
        step_id="record_metrics",
    )
    assert metrics.record.called


def test_assert_comment_activity_observable() -> None:
    trace = ReadingTrace()
    trace.window_done_count = 1
    assert_comment_activity_observable(comment_runs=[], trace=trace)


def test_assert_compaction_failure_does_not_block_comments() -> None:
    trace = ReadingTrace()
    trace.window_done_count = 2
    assert_compaction_failure_does_not_block_comments(
        comment_runs=[{"agent": "ParagraphCommentAgent"}],
        trace=trace,
        failed_job={"status": "failed"},
    )


def test_assert_chapter_summary_in_subsequent_context() -> None:
    assert_chapter_summary_in_subsequent_context(
        {
            "components": [
                {
                    "name": "chapter_compressed_summary",
                    "included": True,
                    "content": {
                        "summary_id": 9,
                        "summary": "compressed facts",
                    },
                }
            ]
        },
        compaction_run={
            "interaction": {
                "final_result": {
                    "summary_id": 9,
                    "summary": "compressed facts",
                    "anchor_excerpts": [],
                }
            }
        },
    )


def test_select_post_compaction_comment_runs() -> None:
    runs = [
        {"job_id": 10, "trace_id": "t-comment"},
        {"job_id": 20, "trace_id": "t-comment-2"},
    ]
    selected = select_post_compaction_comment_runs(
        runs,
        compaction_job_id=15,
        compaction_trace_ids={"t-compaction"},
    )
    assert len(selected) == 1
    assert selected[0]["job_id"] == 20
