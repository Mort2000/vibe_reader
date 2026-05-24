from __future__ import annotations

import json
import time

import pytest

from tests.system_verify.report_generator import _compaction_jobs_summary_lines
from tests.system_verify.scenario import StepAssertionError
from tests.system_verify.scenarios.common import CompactionNoopTracker


def test_compaction_noop_tracker_raises_after_grace_period() -> None:
    tracker = CompactionNoopTracker()
    tracker.first_seen_at = time.monotonic() - 31.0
    with pytest.raises(StepAssertionError) as exc:
        tracker.check(
            done_job={"id": 9, "status": "done"},
            has_agent_run=False,
            scenario_id="S4_long_context",
            jobs=[{"id": 9, "status": "done"}],
        )
    assert exc.value.assertion == "compaction_noop_done"


def test_compaction_noop_tracker_resets_when_agent_run_present() -> None:
    tracker = CompactionNoopTracker()
    tracker.first_seen_at = time.monotonic()
    tracker.check(
        done_job={"id": 9, "status": "done"},
        has_agent_run=True,
        scenario_id="S4_long_context",
    )
    assert tracker.first_seen_at is None


def test_compaction_jobs_summary_lines(tmp_path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    row = {
        "scenario_id": "S4_long_context",
        "min_job_id": 3,
        "summary": {"done": 1, "skipped": 2, "failed": 0, "other": 0},
    }
    (audit_dir / "compaction_jobs.ndjson").write_text(
        json.dumps(row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lines = _compaction_jobs_summary_lines(tmp_path)
    assert any("S4_long_context" in line for line in lines)
    assert any("total:" in line for line in lines)
