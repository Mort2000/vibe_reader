"""Characterization tests for system_verify run artifacts (Phase 0 baseline).

These tests lock the shape of run_manifest, scenario_results, api_requests,
audit layout, and report generation so later refactor phases can detect
regressions without re-running full integration suites.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.system_verify.report_generator import generate_reports

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "baseline"

MANIFEST_REQUIRED_KEYS = frozenset(
    {
        "run_id",
        "started_at",
        "ended_at",
        "git_commit",
        "git_dirty",
        "suite",
        "target_url",
        "backend_version",
        "llm_mode",
        "param_set",
        "param_set_llm_mode",
        "stub_profile",
        "llm_stub_provider",
        "aimock_version",
        "aimock_base_url",
        "aimock_fixture_hash",
        "aimock_profile_hash",
        "real_llm",
        "model",
        "llm_base_url_hash",
        "real_llm_call_count",
        "real_llm_input_tokens",
        "real_llm_output_tokens",
        "real_llm_max_input_tokens_single",
        "real_llm_max_output_tokens_single",
        "real_llm_total_cost_usd",
        "real_llm_cost_guardrail_status",
        "real_llm_budget_exceeded",
        "real_llm_budget_reason",
        "real_llm_phase_coverage",
        "usage_source",
        "corpus_sha256",
        "config_hash",
        "security_checks",
        "target_data_dir",
        "data_lifecycle",
    }
)

SCENARIO_RESULT_KEYS = frozenset(
    {
        "scenario_id",
        "description",
        "status",
        "started_at",
        "ended_at",
        "steps",
        "failure_summary",
    }
)

STEP_KEYS = frozenset(
    {
        "step_id",
        "description",
        "status",
        "duration_ms",
        "assertions_passed",
        "assertions_total",
        "errors",
        "trace_id",
        "request_id",
        "failure_context",
    }
)

API_REQUEST_KEYS = frozenset(
    {
        "method",
        "url",
        "status_code",
        "duration_ms",
        "error",
        "trace_id",
        "request_id",
        "verify_run_id",
        "verify_scenario_id",
        "verify_step_id",
        "request_body_summary",
        "response_body_summary",
        "created_at",
    }
)

MVP_SCENARIO_IDS = (
    "S0_connectivity",
    "S1_book_import",
    "S2_continuous_reading",
    "S3_fast_scroll",
    "S4_long_context",
    "S5_direct_chat",
    "S6_followup_chat",
)

# Phase 0 baseline fixtures were captured before S5/S6 existed.
MVP_BASELINE_SCENARIO_IDS = MVP_SCENARIO_IDS[:5]

AUDIT_TOP_LEVEL_NDJSON = frozenset(
    {
        "agent_invocations.ndjson",
        "audit_safety_report.ndjson",
    }
)

AUDIT_SUBDIRS = frozenset(
    {
        "agent_interactions",
        "agent_reports",
        "contexts",
        "prompts",
        "samples",
    }
)

MVP_AUDIT_EXTRA_NDJSON = frozenset(
    {
        "comments.ndjson",
        "compaction_jobs.ndjson",
        "compaction_summaries.ndjson",
        "prompt_manifest_index.ndjson",
    }
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_ndjson(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _baseline_dirs() -> list[Path]:
    return sorted(p for p in FIXTURES.iterdir() if p.is_dir())


@pytest.mark.parametrize("baseline_dir", _baseline_dirs(), ids=lambda p: p.name)
def test_baseline_manifest_has_required_keys(baseline_dir: Path) -> None:
    manifest = _read_json(baseline_dir / "run_manifest.json")
    missing = MANIFEST_REQUIRED_KEYS - manifest.keys()
    assert not missing, f"{baseline_dir.name}: missing manifest keys {sorted(missing)}"


@pytest.mark.parametrize("baseline_dir", _baseline_dirs(), ids=lambda p: p.name)
def test_baseline_scenario_results_shape(baseline_dir: Path) -> None:
    scenarios = _read_ndjson(baseline_dir / "scenario_results.ndjson")
    inventory = _read_json(baseline_dir / "inventory.json")
    assert len(scenarios) == inventory["scenario_count"]

    for scenario in scenarios:
        missing = SCENARIO_RESULT_KEYS - scenario.keys()
        assert not missing, f"missing scenario keys {sorted(missing)}"
        assert scenario["steps"], "scenario must have at least one step"
        for step in scenario["steps"]:
            step_missing = STEP_KEYS - step.keys()
            assert not step_missing, f"missing step keys {sorted(step_missing)}"


@pytest.mark.parametrize("baseline_dir", _baseline_dirs(), ids=lambda p: p.name)
def test_baseline_api_requests_shape(baseline_dir: Path) -> None:
    rows = _read_ndjson(baseline_dir / "api_requests.ndjson")
    assert rows, "api_requests sample must not be empty"
    for row in rows:
        missing = API_REQUEST_KEYS - row.keys()
        assert not missing, f"missing api request keys {sorted(missing)}"


def test_mvp_stub_baseline_scenario_ids() -> None:
    scenarios = _read_ndjson(FIXTURES / "mvp_stub" / "scenario_results.ndjson")
    assert [s["scenario_id"] for s in scenarios] == list(MVP_BASELINE_SCENARIO_IDS)


def test_mvp_stub_manifest_stub_fields() -> None:
    manifest = _read_json(FIXTURES / "mvp_stub" / "run_manifest.json")
    assert manifest["llm_mode"] == "stub"
    assert manifest["param_set"] == "mvp"
    assert manifest["stub_profile"] == "mvp_default"
    assert manifest["real_llm"] is False
    assert manifest["aimock_fixture_hash"].startswith("sha256:")
    assert manifest["aimock_profile_hash"].startswith("sha256:")


def test_mvp_stub_suite_smoke_alias() -> None:
    """``smoke`` and ``mvp`` CLI suites both invoke ``run_mvp_suite`` (S0–S4).

    Phase 0 baseline was captured with ``--suite smoke``; only ``manifest.suite``
    differs from ``--suite mvp``. Artifact shape and scenario set are identical.
    """
    manifest = _read_json(FIXTURES / "mvp_stub" / "run_manifest.json")
    assert manifest["suite"] in {"smoke", "mvp"}


def test_r1_a2_stub_manifest_phase_coverage() -> None:
    manifest = _read_json(FIXTURES / "r1_a2_stub" / "run_manifest.json")
    assert manifest["param_set"] == "r1_a2_stub"
    assert manifest["real_llm_phase_coverage"]["A2_comments"] is True
    assert manifest["real_llm_phase_coverage"]["A3_compaction"] is False


def test_r1_a3_stub_manifest_phase_coverage() -> None:
    manifest = _read_json(FIXTURES / "r1_a3_stub" / "run_manifest.json")
    assert manifest["param_set"] == "r1_a3_stub"
    assert manifest["real_llm_phase_coverage"]["A2_comments"] is False
    assert manifest["real_llm_phase_coverage"]["A3_compaction"] is True


@pytest.mark.parametrize(
    ("baseline_name", "expected_audit_count", "expected_total_files"),
    [
        ("mvp_stub", 47, 57),
        ("r1_a2_stub", 14, 24),
        ("r1_a3_stub", 28, 38),
    ],
)
def test_baseline_inventory_file_counts(
    baseline_name: str,
    expected_audit_count: int,
    expected_total_files: int,
) -> None:
    inventory = _read_json(FIXTURES / baseline_name / "inventory.json")
    assert inventory["audit_file_count"] == expected_audit_count
    assert inventory["total_files"] == expected_total_files
    assert inventory["report_files"] == ["failures.md", "metrics.json", "summary.md"]


@pytest.mark.parametrize("baseline_dir", _baseline_dirs(), ids=lambda p: p.name)
def test_baseline_inventory_internal_consistency(baseline_dir: Path) -> None:
    inventory = _read_json(baseline_dir / "inventory.json")
    manifest = _read_json(baseline_dir / "run_manifest.json")

    assert inventory["source_run_id"] == manifest["run_id"]
    assert inventory["audit_file_count"] == len(inventory["audit_files"])
    assert set(inventory["report_files"]) == {
        "failures.md",
        "metrics.json",
        "summary.md",
    }


def test_baseline_audit_directory_layout() -> None:
    for baseline_name in ("mvp_stub", "r1_a2_stub", "r1_a3_stub"):
        inventory = _read_json(FIXTURES / baseline_name / "inventory.json")
        audit_files = set(inventory["audit_files"])

        for required in AUDIT_TOP_LEVEL_NDJSON:
            assert required in audit_files, f"{baseline_name}: missing {required}"

        present_subdirs = {
            path.split("/", 1)[0]
            for path in audit_files
            if "/" in path
        }
        assert AUDIT_SUBDIRS <= present_subdirs, (
            f"{baseline_name}: missing audit subdirs "
            f"{sorted(AUDIT_SUBDIRS - present_subdirs)}"
        )


def test_mvp_stub_audit_extra_ndjson_in_inventory() -> None:
    inventory = _read_json(FIXTURES / "mvp_stub" / "inventory.json")
    audit_files = set(inventory["audit_files"])
    missing = MVP_AUDIT_EXTRA_NDJSON - audit_files
    assert not missing, f"mvp_stub inventory missing audit ndjson {sorted(missing)}"


def test_report_generator_reads_mvp_baseline(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for name in ("run_manifest.json", "scenario_results.ndjson", "metrics.ndjson"):
        shutil.copy(FIXTURES / "mvp_stub" / name, run_dir / name)

    reports = generate_reports(run_dir)
    assert reports["summary"].exists()
    assert reports["metrics"].exists()
    assert reports["failures"].exists()

    summary = reports["summary"].read_text(encoding="utf-8")
    assert "Vibe Reader Verify Summary" in summary
    assert "llm_mode: stub" in summary
    assert "param_set: mvp" in summary
