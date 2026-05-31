"""Run specification, user clock, budget guards, and orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifact_store import ArtifactStore, SafetyFinding
from .corpus import CorpusCatalog, CorpusRequirement
from .driver import AppFacade, TargetClient, UserFacade
from .evidence import EvidenceHub, LLMView
from .models import Correlation
from .observability import BackendObservability
from .provider import (
    ProviderHarness,
    ProviderSession,
    StubProfile,
    update_default_correlation,
)
from .scenario import (
    ScenarioContext,
    ScenarioDefinition,
    ScenarioParameters,
    ScenarioRegistry,
    ScenarioResult,
    execute_post_checks,
    execute_scenario,
)


@dataclass(frozen=True)
class UserModel:
    """Time behavior used by the same scripts in stub and real modes."""

    reading_paragraphs_per_second: float = 4.0
    page_delay_s: float = 0.25
    patience_s: float = 30.0
    poll_interval_s: float = 0.1


@dataclass(frozen=True)
class Budget:
    max_calls: int = 100
    max_tokens: int = 1_000_000
    max_duration_s: float = 600.0
    max_cost_usd: float = 0.0


@dataclass(frozen=True)
class Profile:
    """How scenarios run; scripts remain mode-agnostic."""

    name: str = "mvp_stub"
    llm_mode: str = "stub"
    user: UserModel = field(default_factory=UserModel)
    budget: Budget = field(default_factory=Budget)
    audit_enabled: bool = False
    backend_agent_evidence: bool = False
    stub: StubProfile = field(default_factory=StubProfile)
    real_base_url: str = ""
    real_api_key: str = ""
    real_model: str = ""


@dataclass(frozen=True)
class RunSpec:
    """Immutable resolved description of one verification run."""

    suite: str
    profile: Profile
    target_url: str
    artifact_root: Path
    run_id: str = field(default_factory=lambda: "run_" + uuid.uuid4().hex[:12])
    scenario_ids: tuple[str, ...] = ()
    corpus_catalog_path: Path | None = None
    params: dict[str, Any] = field(default_factory=dict)
    seed: int = 20260522

    @property
    def digest(self) -> str:
        data = asdict(self)
        data.pop("run_id", None)
        data.pop("artifact_root", None)
        payload = json.dumps(data, sort_keys=True, default=str).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    scenarios: list[dict[str, str]]
    artifact_dir: Path
    error: str = ""


class UserClock:
    """Apply pacing only when it conveys real user experience."""

    def __init__(
        self,
        profile: Profile,
        *,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ):
        self.profile = profile
        self._sleep = sleep

    async def reading(self, paragraphs: int) -> None:
        if self.profile.llm_mode == "real" and paragraphs > 0:
            await self._sleep(
                paragraphs / self.profile.user.reading_paragraphs_per_second
            )

    async def paging(self) -> None:
        if self.profile.llm_mode == "real" and self.profile.user.page_delay_s > 0:
            await self._sleep(self.profile.user.page_delay_s)

    async def waiting(self, seconds: float) -> None:
        if self.profile.llm_mode == "real" and seconds > 0:
            await self._sleep(min(seconds, self.profile.user.patience_s))

    async def polling(self) -> None:
        interval = max(0.001, self.profile.user.poll_interval_s)
        await self._sleep(min(interval, self.profile.user.patience_s))

    def patience_s(self) -> float:
        return self.profile.user.patience_s


class BudgetExceeded(RuntimeError):
    pass


class RunEngine:
    """Compose modules while keeping business assertions in user scripts."""

    def __init__(
        self,
        registry: ScenarioRegistry,
        *,
        provider: ProviderHarness | None = None,
        client_factory: Callable[..., TargetClient] = TargetClient,
        environment_applier: Callable[[dict[str, str]], None] | None = None,
        target_preparer: Callable[[RunSpec, ProviderSession], Any] | None = None,
        target_cleanup: Callable[[Any], None] | None = None,
    ):
        self.registry = registry
        self.provider = provider or ProviderHarness()
        self.client_factory = client_factory
        self._uses_default_environment = environment_applier is None
        self.environment_applier = environment_applier or os.environ.update
        self.target_preparer = target_preparer
        self.target_cleanup = target_cleanup

    async def run(self, spec: RunSpec) -> RunResult:
        started = time.monotonic()
        wall_started = datetime.now(UTC)
        store = ArtifactStore(
            spec.artifact_root,
            spec.run_id,
            audit_enabled=spec.profile.audit_enabled,
        )
        store.start()
        evidence = EvidenceHub(store=store, audit_enabled=spec.profile.audit_enabled)
        session: ProviderSession | None = None
        target_handle: Any = None
        client: TargetClient | None = None
        previous_env: dict[str, str | None] | None = None
        results: list[dict[str, str]] = []
        resolved_corpora: list[dict[str, Any]] = []
        error = ""
        status = "passed"
        cleanup_errors: list[str] = []
        try:
            run_correlation = Correlation(run_id=spec.run_id)
            session = self.provider.prepare(
                mode=spec.profile.llm_mode,
                evidence=evidence,
                profile=spec.profile.stub,
                default_correlation=run_correlation,
                real_base_url=spec.profile.real_base_url,
                real_api_key=spec.profile.real_api_key,
                real_model=spec.profile.real_model,
                real_budget=spec.profile.budget,
            )
            if self._uses_default_environment:
                previous_env = snapshot_environment(session.backend_env)
            self.environment_applier(session.backend_env)
            if self.target_preparer is not None:
                target_handle = self.target_preparer(spec, session)
            correlation = run_correlation
            client = self.client_factory(
                spec.target_url,
                evidence=evidence,
                correlation=correlation,
            )
            observability = BackendObservability(client)
            await verify_runtime(observability)
            clock = UserClock(spec.profile)
            definitions = self.registry.select(
                suite=spec.suite,
                profile=spec.profile.name,
                scenario_ids=spec.scenario_ids,
            )
            if not definitions:
                raise LookupError("no scenarios selected")
            for definition in definitions:
                enforce_budget(spec.profile.budget, evidence, started)
                client.scoped(scenario_id=definition.id, step_id="scenario")
                update_default_correlation(session, client.correlation)
                context = ScenarioContext(
                    app=AppFacade(client, clock=clock, evidence=evidence),
                    user=UserFacade(clock=clock, evidence=evidence),
                    llm=LLMView(evidence),
                    observability=observability,
                    backend_agent_evidence=spec.profile.backend_agent_evidence,
                    params=build_scenario_parameters(
                        spec,
                        definition.corpus_purpose,
                        resolved_corpora=resolved_corpora,
                    ),
                )
                remaining = max(
                    0.001,
                    spec.profile.budget.max_duration_s - (time.monotonic() - started),
                )
                async with asyncio.timeout(remaining):
                    result = await execute_scenario(definition, context)
                    if result.status == "passed":
                        result = await refresh_evidence_and_post_check(
                            definition,
                            context,
                        )
                results.append(result.to_dict())
                if result.status != "passed":
                    status = "failed"
                    error = result.error
                    store.write_failure(error, result.to_dict())
                    break
                enforce_budget(spec.profile.budget, evidence, started)
            enforce_budget(spec.profile.budget, evidence, started)
        except Exception as exc:
            status = "failed"
            error = str(exc)
            store.write_failure(error)
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as exc:
                    cleanup_errors.append(f"client.close: {exc}")
            if target_handle is not None and self.target_cleanup is not None:
                try:
                    self.target_cleanup(target_handle)
                except Exception as exc:
                    cleanup_errors.append(f"target.cleanup: {exc}")
            if session is not None:
                try:
                    self.provider.cleanup(session)
                except Exception as exc:
                    cleanup_errors.append(f"provider.cleanup: {exc}")
            if previous_env is not None:
                restore_environment(previous_env)

        if cleanup_errors:
            status = "failed"
            if not error:
                error = "; ".join(cleanup_errors)
            store.write_failure(error, {"cleanup_errors": cleanup_errors})
        findings = store.scan_secrets()
        manifest = build_manifest(
            spec,
            session,
            status,
            evidence,
            findings,
            started,
            wall_started,
            resolved_corpora=resolved_corpora,
        )
        required_gaps = required_evidence_gaps(
            spec.profile, manifest.get("evidence_gaps", [])
        )
        if status == "passed" and required_gaps:
            status = "failed"
            error = "required verify evidence not observed"
            store.write_failure(error, {"evidence_gaps": required_gaps})
            manifest = build_manifest(
                spec,
                session,
                status,
                evidence,
                findings,
                started,
                wall_started,
                resolved_corpora=resolved_corpora,
            )
        findings = finalize_safety_findings(
            store,
            status=status,
            scenarios=results,
            manifest=manifest,
            findings=findings,
        )
        if findings:
            status = "failed"
            error = "secret safety scan failed"
            store.write_failure(
                error, {"findings": [asdict(item) for item in findings]}
            )
            manifest = build_manifest(
                spec,
                session,
                status,
                evidence,
                findings,
                started,
                wall_started,
                resolved_corpora=resolved_corpora,
            )
        store.write_manifest(manifest)
        store.write_summary(
            status=status,
            scenarios=results,
            findings=findings,
            manifest=manifest,
            evidence_gaps=manifest.get("evidence_gaps", []),
        )
        return RunResult(
            run_id=spec.run_id,
            status=status,
            scenarios=results,
            artifact_dir=store.run_dir,
            error=error,
        )


async def verify_runtime(
    observability: BackendObservability,
) -> dict[str, Any]:
    """Validate the formal runtime endpoint before driving scenarios."""
    runtime = await observability.runtime()
    if runtime.get("verify_mode") is not True:
        raise RuntimeError(f"backend verify mode is not enabled: {runtime!r}")
    llm = runtime.get("llm")
    if not isinstance(llm, dict) or llm.get("base_url_configured") is not True:
        raise RuntimeError(f"backend LLM base URL is not configured: {llm!r}")
    return runtime


async def collect_agent_invocations_if_needed(
    context: ScenarioContext,
    *,
    scenario_id: str,
) -> int:
    """Import backend-recorded Agent runs when local provider evidence is absent."""
    if context.llm.calls(scenario_id=scenario_id):
        return 0
    if not context.backend_agent_evidence:
        return 0
    return await context.observability.collect_agent_invocations_if_available(
        context.llm.hub,
        scenario_id=scenario_id,
    )


async def refresh_evidence_and_post_check(
    definition: ScenarioDefinition,
    context: ScenarioContext,
) -> ScenarioResult:
    try:
        await collect_agent_invocations_if_needed(
            context,
            scenario_id=definition.id,
        )
    except Exception as exc:
        return ScenarioResult(
            id=definition.id,
            status="failed",
            error=str(exc) or repr(exc),
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
    return await execute_post_checks(definition, context)


def enforce_budget(budget: Budget, evidence: EvidenceHub, started: float) -> None:
    calls = evidence.invocations
    total_tokens = sum(call.usage.total for call in calls)
    total_cost = sum(call.usage.cost_usd for call in calls)
    if len(calls) > budget.max_calls:
        raise BudgetExceeded(f"LLM call budget exceeded: {len(calls)}")
    if total_tokens > budget.max_tokens:
        raise BudgetExceeded(f"LLM token budget exceeded: {total_tokens}")
    if budget.max_cost_usd >= 0 and total_cost > budget.max_cost_usd:
        raise BudgetExceeded(f"LLM cost budget exceeded: {total_cost:.6f}")
    duration = time.monotonic() - started
    if duration > budget.max_duration_s:
        raise BudgetExceeded(f"run duration budget exceeded: {duration:.3f}s")


def snapshot_environment(values: dict[str, str]) -> dict[str, str | None]:
    return {key: os.environ.get(key) for key in values}


def restore_environment(snapshot: dict[str, str | None]) -> None:
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def build_scenario_parameters(
    spec: RunSpec,
    corpus_purpose: str,
    *,
    resolved_corpora: list[dict[str, Any]] | None = None,
) -> ScenarioParameters:
    values = dict(spec.params)
    if not corpus_purpose:
        return ScenarioParameters(values=values)
    if spec.corpus_catalog_path is None:
        raise ValueError(
            f"scenario requires corpus purpose {corpus_purpose!r}, "
            "but RunSpec.corpus_catalog_path is not set"
        )
    catalog = CorpusCatalog(spec.corpus_catalog_path)
    resolved = catalog.resolve(
        CorpusRequirement(
            corpus_purpose,
            real_llm=spec.profile.llm_mode == "real",
        )
    )
    manifest = catalog.resolved_manifest(resolved)
    if resolved_corpora is not None and manifest not in resolved_corpora:
        resolved_corpora.append(manifest)
    return ScenarioParameters(
        corpus=resolved.entry.path,
        probe=resolved.probe,
        values=values,
    )


def finalize_safety_findings(
    store: ArtifactStore,
    *,
    status: str,
    scenarios: list[dict[str, str]],
    manifest: dict[str, Any],
    findings: list[SafetyFinding],
) -> list[SafetyFinding]:
    summary_text = store.render_summary(
        status=status,
        scenarios=scenarios,
        findings=findings,
        manifest=manifest,
        evidence_gaps=manifest.get("evidence_gaps", []),
    )
    manifest_text = json.dumps(jsonable_manifest(manifest), ensure_ascii=False)
    all_findings = [
        *findings,
        *store.scan_text("run_manifest.json", manifest_text),
        *store.scan_text("reports/summary.md", summary_text),
    ]
    return dedupe_findings(all_findings)


def jsonable_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(manifest, default=str, ensure_ascii=False))


def dedupe_findings(findings: list[SafetyFinding]) -> list[SafetyFinding]:
    seen: set[tuple[str, str]] = set()
    result: list[SafetyFinding] = []
    for finding in findings:
        key = (finding.path, finding.pattern)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


_REQUIRED_REAL_MODE_GAP_KEYS = frozenset(
    {
        "real_mode_agent_invocation_usage_not_observed",
        "real_mode_provider_or_framework_usage_not_observed",
        "real_mode_token_usage_not_observed",
        "real_mode_cost_usage_not_observed",
    }
)


def required_evidence_gaps(profile: Profile, gaps: list[str]) -> list[str]:
    """Return evidence gaps that make the run result unverifiable."""
    if profile.llm_mode != "real":
        return []
    return [
        gap
        for gap in gaps
        if gap.split(":", 1)[0] in _REQUIRED_REAL_MODE_GAP_KEYS
    ]


def build_manifest(
    spec: RunSpec,
    session: ProviderSession | None,
    status: str,
    evidence: EvidenceHub,
    findings: list[Any],
    started: float,
    wall_started: datetime,
    *,
    resolved_corpora: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ended = datetime.now(UTC)
    gaps = evidence_gaps(evidence, spec.profile)
    return {
        "artifact_schema": "vibe-verify-artifacts/v1",
        "run_id": spec.run_id,
        "status": status,
        "suite": spec.suite,
        "profile": spec.profile.name,
        "target_url": spec.target_url,
        "llm_mode": spec.profile.llm_mode,
        "model": session.model if session is not None else "",
        "usage_source": session.usage_source if session is not None else "",
        "stub_profile_hash": session.stub_profile_hash if session is not None else "",
        "run_spec_hash": spec.digest,
        "seed": spec.seed,
        "started_at": wall_started.isoformat(),
        "ended_at": ended.isoformat(),
        "user_model": asdict(spec.profile.user),
        "budget": asdict(spec.profile.budget),
        "audit_enabled": spec.profile.audit_enabled,
        "audit_policy": "full" if spec.profile.audit_enabled else "sanitized",
        "llm_call_count": len(evidence.invocations),
        "token_total": sum(call.usage.total for call in evidence.invocations),
        "cost_usd": sum(call.usage.cost_usd for call in evidence.invocations),
        "duration_ms": (time.monotonic() - started) * 1000,
        "safety_findings": [asdict(item) for item in findings],
        "evidence_gaps": gaps,
        "backend_env_applied": bool(session.backend_env)
        if session is not None
        else False,
        "corpus": resolved_corpora or [],
        "artifact_paths": {
            "summary": "reports/summary.md",
            "api": "evidence/api.ndjson",
            "sse": "evidence/sse.ndjson",
            "user_interactions": "evidence/user_interactions.ndjson",
            "agent_invocations": "evidence/agent_invocations.ndjson",
            "stub_journal": "stub/journal.ndjson",
            "failure": "failure/snapshot.json",
            "audit": "audit/",
        },
    }


def evidence_gaps(evidence: EvidenceHub, profile: Profile | None = None) -> list[str]:
    gaps: list[str] = []
    uncorrelated = [
        item.id for item in evidence.invocations if not item.correlation.run_id
    ]
    if uncorrelated:
        gaps.append(
            "agent_invocation_missing_run_correlation: "
            + ", ".join(uncorrelated[:5])
            + (" ..." if len(uncorrelated) > 5 else "")
        )
    missing_trace = [
        item.id for item in evidence.invocations if not item.correlation.trace_id
    ]
    if missing_trace:
        gaps.append(
            "agent_invocation_missing_trace_correlation: "
            + ", ".join(missing_trace[:5])
            + (" ..." if len(missing_trace) > 5 else "")
        )
    missing_scenario = [
        item.id for item in evidence.invocations if not item.correlation.scenario_id
    ]
    if missing_scenario:
        gaps.append(
            "agent_invocation_missing_scenario_correlation: "
            + ", ".join(missing_scenario[:5])
            + (" ..." if len(missing_scenario) > 5 else "")
        )
    missing_step = [
        item.id for item in evidence.invocations if not item.correlation.step_id
    ]
    if missing_step:
        gaps.append(
            "agent_invocation_missing_step_correlation: "
            + ", ".join(missing_step[:5])
            + (" ..." if len(missing_step) > 5 else "")
        )
    if profile is not None and profile.llm_mode == "real":
        if not evidence.invocations:
            gaps.append("real_mode_agent_invocation_usage_not_observed")
        else:
            observed_usage = [
                item
                for item in evidence.invocations
                if item.usage.source in {"provider", "framework"}
            ]
            if not observed_usage:
                gaps.append("real_mode_provider_or_framework_usage_not_observed")
            missing_tokens = [
                item.id for item in observed_usage if item.usage.total <= 0
            ]
            if missing_tokens:
                gaps.append(
                    "real_mode_token_usage_not_observed: "
                    + ", ".join(missing_tokens[:5])
                    + (" ..." if len(missing_tokens) > 5 else "")
                )
        if profile.budget.max_cost_usd > 0 and not any(
            item.usage.cost_usd > 0 for item in evidence.invocations
        ):
            gaps.append("real_mode_cost_usage_not_observed")
    return gaps
