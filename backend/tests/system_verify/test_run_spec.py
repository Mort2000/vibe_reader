from __future__ import annotations

import pytest

from tests.system_verify.core.run_spec import (
    build_verify_config_from_run_spec,
    resolve_run_spec,
    resolve_run_spec_from_pytest,
)


def test_resolve_run_spec_default_mvp() -> None:
    spec = resolve_run_spec(suite="mvp")
    assert spec.suite == "mvp"
    assert spec.param_set_name == "mvp"
    assert spec.profile_name == "mvp_stub"
    assert spec.llm_mode == "stub"
    assert spec.coverage is None


def test_resolve_run_spec_real_happy_path_a2_stub() -> None:
    spec = resolve_run_spec(
        suite="real-happy-path",
        coverage="A2",
        llm_mode_override="stub",
    )
    assert spec.param_set_name == "r1_a2_stub"
    assert spec.profile_name == "r1_a2_stub"
    assert spec.llm_mode == "stub"
    assert spec.coverage == "A2"


def test_resolve_run_spec_real_happy_path_a3_real() -> None:
    spec = resolve_run_spec(
        suite="real-happy-path",
        coverage="A3",
        llm_mode_override="real",
    )
    assert spec.param_set_name == "r1_a3_real"
    assert spec.profile_name == "r1_a3_real"
    assert spec.llm_mode == "real"


def test_resolve_run_spec_explicit_param_set() -> None:
    spec = resolve_run_spec(param_set="r1_a2_real", suite="real-happy-path")
    assert spec.param_set_name == "r1_a2_real"
    assert spec.llm_mode == "real"


def test_resolve_run_spec_llm_mode_conflict() -> None:
    with pytest.raises(ValueError, match="conflicts"):
        resolve_run_spec(
            param_set="r1_a2_real",
            llm_mode_override="stub",
        )


def test_build_verify_config_from_run_spec() -> None:
    spec = resolve_run_spec(param_set="r1_a3_real", suite="real-happy-path")
    config = build_verify_config_from_run_spec(spec)
    assert config.params.name == "r1_a3_real"
    assert config.llm.mode == "real"
    assert config.params.long_flow.test_compaction_min_source_tokens == 5000


def test_run_spec_is_immutable() -> None:
    spec = resolve_run_spec(suite="mvp")
    with pytest.raises(AttributeError):
        spec.suite = "other"  # type: ignore[misc]


def test_resolve_run_spec_from_pytest_uses_options(pytestconfig: pytest.Config) -> None:
    class _Request:
        config = pytestconfig

    spec = resolve_run_spec_from_pytest(_Request())  # type: ignore[arg-type]
    assert spec.suite == "mvp"
    assert spec.param_set_name == "mvp"


def test_resolve_run_spec_matches_load_verify_config_for_cli() -> None:
    """_cmd_run RunSpec path should match legacy _load_run_config resolution."""
    from tests.system_verify.core.config_loader import load_verify_config

    cases = [
        {"suite": "mvp"},
        {
            "suite": "real-happy-path",
            "coverage": "A2",
            "llm_mode_override": "stub",
        },
        {
            "suite": "real-happy-path",
            "coverage": "A3",
            "param_set": "r1_a3_real",
            "llm_mode_override": "real",
        },
    ]
    for kwargs in cases:
        spec = resolve_run_spec(**kwargs)
        legacy = load_verify_config(
            spec.config_path,
            param_set=spec.param_set_name if kwargs.get("param_set") else None,
            suite=kwargs.get("suite"),
            coverage=kwargs.get("coverage", "A2"),
            llm_mode_override=kwargs.get("llm_mode_override"),
        )
        resolved = build_verify_config_from_run_spec(spec)
        assert resolved.params.name == legacy.params.name
        assert resolved.llm.mode == legacy.llm.mode
        assert resolved.run.suite == legacy.run.suite
