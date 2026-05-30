"""Unit tests for comment assertion helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.system_verify.assertions.comments import (
    assert_comment_ids_stable,
    assert_comments_valid,
    assert_no_comment_recreated_events,
    collect_validation_failures,
    progress_update_was_deduped,
    window_covers_paragraph,
    window_is_no_call,
)
from tests.system_verify.assertions.runtime import assert_reading_not_blocked_timing
from tests.system_verify.core.scenario import StepAssertionError
from tests.system_verify.sse_collector import SSEEvent


def test_window_is_no_call_explicit_marker() -> None:
    assert window_is_no_call({"no_call": True, "status": "done"}, []) is True


def test_window_is_no_call_zero_ready() -> None:
    window = {
        "status": "done",
        "comments_ready_count": 0,
        "comments_target_count": 3,
    }
    assert window_is_no_call(window, []) is True


def test_window_is_no_call_with_comments() -> None:
    window = {"status": "done", "comments_ready_count": 0, "comments_target_count": 3}
    assert window_is_no_call(window, [{"paragraph_idx": 1}]) is False


def test_collect_validation_failures_from_comments_and_telemetry() -> None:
    comments = [
        {
            "paragraph_idx": 2,
            "trace_id": "t1",
            "validation_failed": True,
            "validation_error": "bad output",
        }
    ]
    window = {
        "comment_telemetry": {
            "validation_failures": [
                {"paragraph_idx": 5, "trace_id": "t2", "reason": "timeout"}
            ]
        }
    }
    failures = collect_validation_failures(comments, window)
    assert len(failures) == 2
    assert failures[0]["paragraph_idx"] == 2
    assert failures[1]["paragraph_idx"] == 5


def test_collect_validation_failures_deduplicates() -> None:
    comments = [
        {
            "paragraph_idx": 1,
            "trace_id": "t1",
            "validation_failed": True,
        },
        {
            "paragraph_idx": 1,
            "trace_id": "t1",
            "validation_failed": True,
        },
    ]
    assert len(collect_validation_failures(comments)) == 1


def test_assert_comments_valid_accepts_no_call_window() -> None:
    window = {"id": 9, "status": "done", "no_call": True}
    assert assert_comments_valid([], window=window) == []


def test_assert_comments_valid_rejects_empty_without_no_call() -> None:
    window = {"id": 9}
    with pytest.raises(StepAssertionError) as exc:
        assert_comments_valid([], window=window, allow_no_call=False)
    assert exc.value.assertion == "comments_or_no_call"


def test_assert_comments_valid_checks_focus_range() -> None:
    window = {
        "id": 1,
        "focus_start_paragraph_idx": 2,
        "focus_end_paragraph_idx": 4,
    }
    comments = [{"paragraph_idx": 5, "window_id": 1, "comment": "ok"}]
    with pytest.raises(StepAssertionError):
        assert_comments_valid(comments, window=window)


def test_progress_update_was_deduped_markers() -> None:
    assert progress_update_was_deduped({}, {"deduped": True}) is True
    assert progress_update_was_deduped({}, {"dedup": True}) is True


def test_progress_update_was_deduped_stable_updated_at() -> None:
    first = {"progress": {"updated_at": "2026-01-01T00:00:00Z"}}
    second = {"progress": {"updated_at": "2026-01-01T00:00:00Z"}}
    assert progress_update_was_deduped(first, second) is True


def test_window_covers_paragraph() -> None:
    window = {"start_paragraph_idx": 1, "end_paragraph_idx": 10}
    assert window_covers_paragraph(window, 5) is True
    assert window_covers_paragraph(window, 11) is False
    assert window_covers_paragraph(None, 1) is False


def test_assert_no_comment_recreated_events_raises() -> None:
    evt = SSEEvent("comment.created", {"paragraph_idx": 3})
    with pytest.raises(StepAssertionError) as exc:
        assert_no_comment_recreated_events([evt], {3: 99}, chapter_idx=1)
    assert exc.value.assertion == "comment_reuse"


def test_assert_comment_ids_stable() -> None:
    assert_comment_ids_stable(
        [{"paragraph_idx": 2, "id": 42}],
        {2: 42},
    )


def test_assert_comment_ids_stable_mismatch() -> None:
    with pytest.raises(StepAssertionError):
        assert_comment_ids_stable(
            [{"paragraph_idx": 2, "id": 99}],
            {2: 42},
        )


def test_assert_reading_not_blocked_timing_passes() -> None:
    assert_reading_not_blocked_timing(100.0, max_duration_ms=5000.0)


def test_assert_reading_not_blocked_timing_fails() -> None:
    with pytest.raises(StepAssertionError) as exc:
        assert_reading_not_blocked_timing(6000.0, max_duration_ms=5000.0)
    assert exc.value.assertion == "reading_not_blocked"


def test_assert_comments_valid_strict_done_without_comments() -> None:
    config = MagicMock()
    config.params.assertions = MagicMock()
    config.params.assertions.strict_done_without_comments = True
    window = {"id": 1, "status": "done"}
    with pytest.raises(StepAssertionError) as exc:
        assert_comments_valid([], window=window, config=config)
    assert exc.value.assertion == "done_with_zero_comments"
