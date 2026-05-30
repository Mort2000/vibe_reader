from __future__ import annotations

import pytest

from tests.system_verify.core.config_loader import (
    apply_param_set,
    load_verify_config,
    resolve_param_set_name,
)


def test_load_default_mvp_param_set() -> None:
    config = load_verify_config("tests/corpus/verify.toml")
    assert config.params.name == "mvp"
    assert config.params.llm_mode == "stub"
    assert config.params.progress_step_delay_ms == 0
    assert config.llm.mode == "stub"
    assert config.llm.stub_profile == "mvp_default"


def test_apply_r1_a2_real_param_set() -> None:
    config = load_verify_config("tests/corpus/verify.toml", param_set="r1_a2_real")
    assert config.params.name == "r1_a2_real"
    assert config.params.llm_mode == "real"
    assert config.params.read_batch_size == 120
    assert config.params.budget.enforce is True
    assert config.metrics.collect_provider_usage is True


def test_apply_r1_a3_real_param_set_matches_probe() -> None:
    config = load_verify_config("tests/corpus/verify.toml", param_set="r1_a3_real")
    assert config.params.name == "r1_a3_real"
    assert config.params.long_flow.test_compaction_min_source_tokens == 5000
    assert config.params.long_flow.test_compaction_min_source_paragraphs == 80


def test_apply_param_set_llm_mode_conflict() -> None:
    config = load_verify_config("tests/corpus/verify.toml")
    with pytest.raises(ValueError, match="conflicts"):
        apply_param_set(config, "r1_a2_real", llm_mode_override="stub")


def test_resolve_real_happy_path_by_llm_mode() -> None:
    config = load_verify_config("tests/corpus/verify.toml")
    stub_name = resolve_param_set_name(
        config,
        suite="real-happy-path",
        coverage="A2",
        llm_mode_hint="stub",
    )
    real_name = resolve_param_set_name(
        config,
        suite="real-happy-path",
        coverage="A3",
        llm_mode_hint="real",
    )
    assert stub_name == "r1_a2_stub"
    assert real_name == "r1_a3_real"
