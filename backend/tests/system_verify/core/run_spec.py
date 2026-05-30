"""Immutable run specification for system verification."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from tests.system_verify.core.config_loader import load_verify_config, resolve_param_set_name
from tests.system_verify.core.config import VerifyConfig

if TYPE_CHECKING:
    from tests.system_verify.core.orchestrator import VerifySessionHandle
from tests.system_verify.profiles.registry import (
    VerificationProfile,
    get_profile,
    resolve_profile,
)

DEFAULT_CONFIG_PATH = "tests/corpus/verify.toml"
DEFAULT_CORPUS_PATH = "tests/corpus/manifest.toml"


@dataclass(frozen=True)
class RunSpec:
    suite: str
    scenario_id: str | None
    profile_name: str
    param_set_name: str
    llm_mode: str
    coverage: str | None
    target_url: str
    corpus_path: str
    config_path: str
    run_id: str | None = None
    spawn_backend: bool = False
    dry_run: bool = False
    keep_data: bool = False

    @property
    def profile(self) -> str:
        """Alias for profile_name (registry lookup key)."""
        return self.profile_name


ScenarioInvokeFn = Callable[["VerifySessionHandle"], Awaitable[None]]


@dataclass(frozen=True)
class ScenarioDefinition:
    """Registry entry describing a runnable verification scenario."""

    id: str
    suite_tags: frozenset[str]
    allowed_profiles: frozenset[str]
    required_probes: tuple[str, ...]
    order: int = 0
    coverage: str | None = None
    requires_corpus: bool = True
    invoke: ScenarioInvokeFn | None = None


def _base_config(
    config_path: str | None = None,
) -> VerifyConfig:
    path = config_path or os.environ.get("VIBE_READER_VERIFY_CONFIG") or DEFAULT_CONFIG_PATH
    return load_verify_config(path, suite=None, param_set=None)


def resolve_run_spec(
    *,
    suite: str | None = None,
    scenario_id: str | None = None,
    param_set: str | None = None,
    coverage: str | None = None,
    llm_mode_override: str | None = None,
    target_url: str | None = None,
    corpus_path: str | None = None,
    config_path: str | None = None,
    run_id: str | None = None,
    spawn_backend: bool = False,
    dry_run: bool = False,
    keep_data: bool = False,
) -> RunSpec:
    """Resolve an immutable RunSpec from CLI/pytest inputs and TOML defaults."""
    base = _base_config(config_path)
    effective_suite = suite or base.run.suite
    effective_coverage = coverage
    effective_llm_mode = (
        llm_mode_override
        or os.environ.get("VIBE_READER_VERIFY_LLM_MODE")
        or base.llm.mode
    )

    param_set_name = resolve_param_set_name(
        base,
        explicit=param_set,
        suite=effective_suite,
        coverage=effective_coverage or "A2",
        llm_mode_hint=effective_llm_mode,
    )
    profile = profile_from_resolved_param_set(base, param_set_name)

    if llm_mode_override and llm_mode_override != profile.llm_mode:
        raise ValueError(
            f"llm_mode {llm_mode_override!r} conflicts with param set "
            f"{param_set_name!r} (llm_mode={profile.llm_mode!r})"
        )

    effective_target = (
        target_url
        or os.environ.get("VIBE_READER_VERIFY_TARGET_URL")
        or base.target.base_url
    )
    effective_corpus = corpus_path or DEFAULT_CORPUS_PATH
    effective_config = config_path or os.environ.get("VIBE_READER_VERIFY_CONFIG") or DEFAULT_CONFIG_PATH

    return RunSpec(
        suite=effective_suite,
        scenario_id=scenario_id,
        profile_name=profile.name,
        param_set_name=param_set_name,
        llm_mode=profile.llm_mode,
        coverage=effective_coverage,
        target_url=effective_target,
        corpus_path=effective_corpus,
        config_path=effective_config,
        run_id=run_id,
        spawn_backend=spawn_backend,
        dry_run=dry_run,
        keep_data=keep_data,
    )


def profile_from_resolved_param_set(
    config: VerifyConfig,
    param_set_name: str,
) -> VerificationProfile:
    return get_profile(param_set_name, config)


def resolve_run_spec_from_pytest(request: pytest.FixtureRequest) -> RunSpec:
    """Build a RunSpec from pytest session options."""
    return resolve_run_spec(
        param_set=request.config.getoption("--param-set"),
        llm_mode_override=request.config.getoption("--llm-mode"),
        run_id=request.config.getoption("--verify-run-id")
        or os.environ.get("VIBE_READER_VERIFY_RUN_ID"),
        spawn_backend=request.config.getoption("--spawn-backend"),
    )


def build_verify_config_from_run_spec(
    spec: RunSpec,
    *,
    config_path: str | None = None,
) -> VerifyConfig:
    """Create a compatible VerifyConfig from a RunSpec exactly once."""
    path = config_path or spec.config_path
    return load_verify_config(
        path,
        param_set=spec.param_set_name,
        suite=spec.suite,
        coverage=spec.coverage or "A2",
        llm_mode_override=spec.llm_mode,
    )


def resolve_profile_for_run_spec(spec: RunSpec) -> VerificationProfile:
    """Resolve VerificationProfile for an existing RunSpec."""
    config = build_verify_config_from_run_spec(spec)
    return resolve_profile(
        config,
        suite=spec.suite,
        coverage=spec.coverage,
        llm_mode=spec.llm_mode,
        explicit_param_set=spec.param_set_name,
    )


def run_spec_for_param_set(
    param_set_name: str,
    *,
    suite: str = "real-happy-path",
    coverage: str | None = None,
    llm_mode_override: str | None = None,
    config_path: str | None = None,
) -> RunSpec:
    """Convenience helper for pytest fixtures targeting a fixed param set."""
    return resolve_run_spec(
        suite=suite,
        param_set=param_set_name,
        coverage=coverage,
        llm_mode_override=llm_mode_override,
        config_path=config_path,
    )
