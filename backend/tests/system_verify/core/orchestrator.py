"""CLI and pytest shared execution pipeline for system verification."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from tests.system_verify.core.config import VerifyConfig, validate_real_llm_config
from tests.system_verify.core.run_spec import (
    RunSpec,
    ScenarioDefinition,
    build_verify_config_from_run_spec,
    resolve_profile_for_run_spec,
)
from tests.system_verify.corpus import CorpusManager
from tests.system_verify.metrics_collector import MetricsAggregator
from tests.system_verify.profiles.registry import profile_name_for_param_set
from tests.system_verify.report_generator import generate_reports
from .run_manager import RunManager
from tests.system_verify.scenarios.registry import select_scenarios_for_run

_R1_PARAM_SET = re.compile(r"^r1_a[234]_(stub|real)$")


@dataclass
class VerifySessionHandle:
    """Shared runtime state for orchestrator-driven scenario execution."""

    spec: RunSpec
    config: VerifyConfig
    run_manager: RunManager
    metrics: MetricsAggregator
    corpus_path: str
    suite_ctx: dict[str, Any]
    corpus: Any | None = None


def build_session_handle(
    *,
    spec: RunSpec,
    config: VerifyConfig,
    run_manager: RunManager,
    metrics: MetricsAggregator,
    corpus_path: str,
    suite_ctx: dict[str, Any] | None = None,
    corpus: Any | None = None,
) -> VerifySessionHandle:
    return VerifySessionHandle(
        spec=spec,
        config=config,
        run_manager=run_manager,
        metrics=metrics,
        corpus_path=corpus_path,
        suite_ctx=suite_ctx if suite_ctx is not None else {},
        corpus=corpus,
    )


def _validate_suite_profile(*, suite: str, config: VerifyConfig, profile_name: str) -> None:
    if suite in ("smoke", "mvp") and config.params.llm_mode != "stub":
        raise RuntimeError(
            f"mvp/smoke suites require a stub param set; got {config.params.name!r} "
            f"(llm_mode={config.params.llm_mode})"
        )
    if suite == "real-happy-path":
        if not _R1_PARAM_SET.match(config.params.name):
            raise RuntimeError(
                "real-happy-path requires a matching param set; "
                f"got {config.params.name!r}"
            )
        if config.is_real_llm:
            errors = validate_real_llm_config(config)
            if errors:
                raise RuntimeError("Real LLM config invalid: " + "; ".join(errors))


def _validate_coverage_param_set(config: VerifyConfig, coverage: str | None) -> None:
    if coverage is None:
        return
    expected_suffix = coverage.upper()
    if not config.params.name.startswith(f"r1_{expected_suffix.lower()}_"):
        raise RuntimeError(
            f"real-happy-path coverage {coverage} requires param set "
            f"r1_{expected_suffix.lower()}_{{stub|real}}; got {config.params.name!r}"
        )


def prepare_corpus(
    mgr: RunManager, config: VerifyConfig, corpus_path: str
) -> CorpusManager:
    """Load, validate, and resolve corpus manifest for a verification run."""
    corpus = CorpusManager(config, corpus_path)
    corpus.load()
    if not corpus.validate():
        errors = "\n".join(corpus.validation_errors)
        raise RuntimeError(f"Corpus validation failed:\n{errors}")

    resolved = corpus.resolve(mgr)
    print(f"Corpus resolved: {resolved}")
    return corpus


def finalize_reports(mgr: RunManager) -> dict[str, Any]:
    """Generate summary artifacts for a completed run."""
    paths = generate_reports(mgr.base_dir)
    print(f"Report written: {paths['summary']}")
    return {key: str(path) for key, path in paths.items()}


def _ensure_corpus(handle: VerifySessionHandle) -> VerifySessionHandle:
    if handle.corpus is not None:
        return handle
    corpus = prepare_corpus(handle.run_manager, handle.config, handle.corpus_path)
    return replace(handle, corpus=corpus)


async def run_scenario(
    handle: VerifySessionHandle,
    scenario_def: ScenarioDefinition,
) -> VerifySessionHandle:
    """Execute a single registered scenario."""
    if scenario_def.invoke is None:
        raise RuntimeError(f"Scenario {scenario_def.id!r} has no invoke handler")

    profile_name = profile_name_for_param_set(handle.config.params.name)
    if profile_name not in scenario_def.allowed_profiles:
        raise RuntimeError(
            f"Scenario {scenario_def.id!r} does not allow profile {profile_name!r}"
        )

    session = _ensure_corpus(handle) if scenario_def.requires_corpus else handle
    await scenario_def.invoke(session)
    if scenario_def.requires_corpus and session.corpus is not None:
        return session
    return handle


async def run_scenarios(
    handle: VerifySessionHandle,
    scenarios: list[ScenarioDefinition],
) -> None:
    """Execute an ordered list of scenarios."""
    current = handle
    for scenario_def in scenarios:
        current = await run_scenario(current, scenario_def)


async def run_suite_scenarios(
    run_manager: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus_path: str,
    *,
    suite: str | None = None,
    coverage: str | None = None,
    scenario_id: str | None = None,
    suite_ctx: dict[str, Any] | None = None,
    spec: RunSpec | None = None,
) -> None:
    """Run all scenarios selected for a suite/profile/coverage combination."""
    effective_spec = spec or RunSpec(
        suite=suite or config.run.suite,
        scenario_id=scenario_id,
        profile_name=profile_name_for_param_set(config.params.name),
        param_set_name=config.params.name,
        llm_mode=config.params.llm_mode,
        coverage=coverage,
        target_url=config.target.base_url,
        corpus_path=corpus_path,
        config_path="tests/corpus/verify.toml",
    )
    effective_suite = suite or effective_spec.suite
    effective_coverage = coverage if coverage is not None else effective_spec.coverage
    profile_name = profile_name_for_param_set(config.params.name)

    _validate_suite_profile(suite=effective_suite, config=config, profile_name=profile_name)
    if effective_suite == "real-happy-path":
        _validate_coverage_param_set(config, effective_coverage)

    scenarios = select_scenarios_for_run(
        suite=effective_suite,
        profile_name=profile_name,
        coverage=effective_coverage,
        scenario_id=scenario_id,
    )
    if not scenarios:
        raise RuntimeError(
            f"No scenarios registered for suite={effective_suite!r}, "
            f"profile={profile_name!r}, coverage={effective_coverage!r}"
        )

    handle = build_session_handle(
        spec=effective_spec,
        config=config,
        run_manager=run_manager,
        metrics=metrics,
        corpus_path=corpus_path,
        suite_ctx=suite_ctx,
    )
    await run_scenarios(handle, scenarios)


async def run_suite_from_spec(spec: RunSpec) -> None:
    """Full CLI-style run from a resolved RunSpec (creates run manager and metrics)."""
    config = build_verify_config_from_run_spec(spec)
    profile = resolve_profile_for_run_spec(spec)
    _validate_suite_profile(suite=spec.suite, config=config, profile_name=profile.name)
    if spec.suite == "real-happy-path":
        _validate_coverage_param_set(config, spec.coverage)

    mgr = RunManager(config, run_id=spec.run_id)
    mgr.start()
    metrics = MetricsAggregator(mgr, config)
    try:
        await run_suite_scenarios(
            mgr,
            config,
            metrics,
            spec.corpus_path,
            suite=spec.suite,
            coverage=spec.coverage,
            scenario_id=spec.scenario_id,
            spec=spec,
        )
    finally:
        mgr.finish()
        mgr.write_manifest()
