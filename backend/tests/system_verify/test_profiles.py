from __future__ import annotations

from tests.system_verify.core.config_loader import load_verify_config
from tests.system_verify.core.run_spec import resolve_run_spec
from tests.system_verify.profiles.registry import (
    get_profile,
    profile_from_param_set,
    profile_name_for_param_set,
    resolve_profile,
)


def test_mvp_param_set_maps_to_mvp_stub_profile() -> None:
    config = load_verify_config("tests/corpus/verify.toml")
    profile = get_profile("mvp", config)
    assert profile.name == "mvp_stub"
    assert profile.param_set_name == "mvp"
    assert profile.llm_mode == "stub"
    assert profile.aimock_profile == "mvp_default"
    assert profile.required_probes == ()


def test_r1_a2_stub_profile_policies() -> None:
    config = load_verify_config("tests/corpus/verify.toml", param_set="r1_a2_stub")
    profile = profile_from_param_set(config.params)
    assert profile.name == "r1_a2_stub"
    assert profile.pacing.max_wait_comment_window_s == 300
    assert profile.budget_policy.enforce is False
    assert profile.assertion_policy.strict_done_without_comments is True
    assert profile.audit_policy.require_agent_artifacts is False
    assert profile.required_probes == ("happy_path_current",)


def test_r1_a2_real_profile_policies() -> None:
    config = load_verify_config("tests/corpus/verify.toml", param_set="r1_a2_real")
    profile = profile_from_param_set(config.params)
    assert profile.name == "r1_a2_real"
    assert profile.llm_mode == "real"
    assert profile.pacing.read_batch_size == 120
    assert profile.long_flow.reading_stop_mode == "cross_chapter"
    assert profile.budget_policy.enforce is True
    assert profile.budget_policy.track_usage is True
    assert profile.audit_policy.require_agent_artifacts is True


def test_r1_a3_real_compaction_audit_policy() -> None:
    config = load_verify_config("tests/corpus/verify.toml", param_set="r1_a3_real")
    profile = profile_from_param_set(config.params)
    assert profile.long_flow.test_compaction_min_source_tokens == 5000
    assert profile.assertion_policy.require_compaction_audit_real is True
    assert profile.audit_policy.require_agent_artifacts is True


def test_resolve_profile_by_suite_and_coverage() -> None:
    config = load_verify_config("tests/corpus/verify.toml")
    stub_profile = resolve_profile(
        config,
        suite="real-happy-path",
        coverage="A2",
        llm_mode="stub",
    )
    real_profile = resolve_profile(
        config,
        suite="real-happy-path",
        coverage="A3",
        llm_mode="real",
    )
    assert stub_profile.name == "r1_a2_stub"
    assert real_profile.name == "r1_a3_real"


def test_profile_name_for_param_set_alias() -> None:
    assert profile_name_for_param_set("mvp") == "mvp_stub"
    assert profile_name_for_param_set("r1_a2_stub") == "r1_a2_stub"


def test_run_spec_profile_matches_registry() -> None:
    spec = resolve_run_spec(
        suite="real-happy-path",
        coverage="A3",
        llm_mode_override="stub",
    )
    config = load_verify_config("tests/corpus/verify.toml", param_set=spec.param_set_name)
    profile = get_profile(spec.profile_name, config)
    assert profile.param_set_name == spec.param_set_name
    assert profile.llm_mode == spec.llm_mode
