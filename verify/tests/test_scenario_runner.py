from __future__ import annotations

import hashlib
import json
import os

import httpx
import pytest

from vibe_verify.driver import TargetClient
from vibe_verify.evidence import EvidenceHub
from vibe_verify.models import AgentInvocation, Correlation, TokenUsage
from vibe_verify.provider import ProviderSession
from vibe_verify.runner import (
    Budget,
    BudgetExceeded,
    Profile,
    RunEngine,
    RunSpec,
    UserClock,
    UserModel,
    enforce_budget,
    evidence_gaps,
    required_evidence_gaps,
)
from vibe_verify.scenario import (
    ScenarioContext,
    ScenarioDefinition,
    ScenarioParameters,
    ScenarioRegistry,
    execute_scenario,
)


class FakeClient(TargetClient):
    def __init__(self, _base_url, *, evidence, correlation):
        super().__init__(
            "http://backend",
            evidence=evidence,
            correlation=correlation,
            client=httpx.AsyncClient(
                base_url="http://backend",
                transport=httpx.MockTransport(self.handle),
            ),
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/verify/"):
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={})

    async def close(self) -> None:
        await self._client.aclose()


class BrokenCloseClient(FakeClient):
    async def close(self) -> None:
        raise RuntimeError("close failed")


class RuntimeFalseClient(FakeClient):
    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/verify/runtime":
            return httpx.Response(200, json={"verify_mode": False})
        return super().handle(request)


class RuntimeMissingBaseURLClient(FakeClient):
    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/verify/runtime":
            return httpx.Response(
                200,
                json={
                    "verify_mode": True,
                    "llm": {"base_url_configured": False},
                },
            )
        return super().handle(request)


class RuntimeMalformedClient(FakeClient):
    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/verify/runtime":
            return httpx.Response(200, json={})
        return super().handle(request)


class AgentRunsClient(FakeClient):
    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/verify/agent-runs":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "invocation_id": "backend-inv-1",
                            "agent_name": "Reader",
                            "verify_run_id": request.url.params.get("run_id"),
                            "verify_scenario_id": request.url.params.get(
                                "scenario_id"
                            ),
                            "verify_step_id": "scenario",
                            "trace_id": "trace",
                            "input_tokens": 8,
                            "output_tokens": 2,
                            "cost_usd": 0.01,
                            "usage_source": "provider",
                        }
                    ],
                    "total": 1,
                },
            )
        return super().handle(request)


class AgentRunsServerErrorClient(FakeClient):
    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/verify/agent-runs":
            return httpx.Response(500, json={"error": "broken"})
        return super().handle(request)


class AgentRunsMalformedClient(FakeClient):
    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/verify/agent-runs":
            return httpx.Response(200, json={})
        return super().handle(request)


class BrokenCleanupProvider:
    def prepare(self, **_kwargs):
        return ProviderSession(
            mode="stub",
            backend_env={"VIBE_READER_LLM_BASE_URL": "http://stub/v1"},
            model="stub",
            usage_source="estimate",
        )

    def cleanup(self, _session) -> None:
        raise RuntimeError("cleanup failed")


class RealNoUsageProvider:
    def prepare(self, **_kwargs):
        return ProviderSession(
            mode="real",
            backend_env={"VIBE_READER_LLM_BASE_URL": "http://real/v1"},
            model="real-model",
            usage_source="provider",
        )

    def cleanup(self, _session) -> None:
        return None


async def passing(_context: ScenarioContext) -> None:
    return None


async def failing(_context: ScenarioContext) -> None:
    raise AssertionError("scenario failed")


async def leaking_secret(_context: ScenarioContext) -> None:
    raise AssertionError("Authorization: Bearer top-secret")


def sync_script(context: ScenarioContext) -> None:
    assert context.params.answer == 42


def test_registry_register_select_and_reject_unknown() -> None:
    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("core", passing, suites=frozenset({"core"})))
    registry.register(
        ScenarioDefinition(
            "real", passing, suites=frozenset({"core"}), profiles=frozenset({"real"})
        )
    )
    assert [item.id for item in registry.select(suite="core", profile="stub")] == [
        "core"
    ]
    with pytest.raises(ValueError):
        registry.register(ScenarioDefinition("core", passing))
    with pytest.raises(LookupError):
        registry.select(suite="core", profile="stub", scenario_ids=("missing",))


async def test_execute_scenario_records_failure() -> None:
    result = await execute_scenario(ScenarioDefinition("failed", failing), None)  # type: ignore[arg-type]
    assert result.status == "failed"
    assert result.error == "scenario failed"
    assert result.error_type == "AssertionError"
    assert "failing" in result.traceback


async def test_execute_scenario_accepts_sync_script_and_immutable_params() -> None:
    params = ScenarioParameters(values={"answer": 42})
    context = ScenarioContext(  # type: ignore[arg-type]
        app=None,
        user=None,
        llm=None,
        observability=None,
        params=params,
    )
    result = await execute_scenario(ScenarioDefinition("sync", sync_script), context)
    assert result.status == "passed"
    with pytest.raises(TypeError):
        params.values["answer"] = 0  # type: ignore[index]


async def test_user_clock_stub_is_noop_and_real_uses_model() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    stub = UserClock(Profile(), sleep=sleep)
    await stub.reading(10)
    await stub.paging()
    await stub.waiting(2)
    assert sleeps == []

    real = UserClock(
        Profile(
            llm_mode="real",
            user=UserModel(reading_paragraphs_per_second=2, patience_s=1),
        ),
        sleep=sleep,
    )
    await real.reading(10)
    await real.paging()
    await real.waiting(2)
    await real.polling()
    assert sleeps == [5, 0.25, 1, 0.1]
    assert real.patience_s() == 1


async def test_run_engine_success_and_failure_artifacts(tmp_path) -> None:
    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("passed", passing))
    result = await RunEngine(registry, client_factory=FakeClient).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id="pass",
        )
    )
    assert result.status == "passed"
    manifest = json.loads((result.artifact_dir / "run_manifest.json").read_text())
    assert manifest["llm_mode"] == "stub"
    assert manifest["usage_source"] == "estimate"
    assert manifest["backend_env_applied"] is True

    failed_registry = ScenarioRegistry()
    failed_registry.register(ScenarioDefinition("failed", failing))
    result = await RunEngine(failed_registry, client_factory=FakeClient).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id="fail",
        )
    )
    assert result.status == "failed"
    failure = json.loads((result.artifact_dir / "failure/snapshot.json").read_text())
    assert failure["context"]["error_type"] == "AssertionError"


async def test_run_engine_restores_default_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_READER_LLM_BASE_URL", "http://previous/v1")
    monkeypatch.delenv("VIBE_READER_LLM_API_KEY", raising=False)
    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("passed", passing))

    result = await RunEngine(registry, client_factory=FakeClient).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
        )
    )

    assert result.status == "passed"
    assert os.environ["VIBE_READER_LLM_BASE_URL"] == "http://previous/v1"
    assert "VIBE_READER_LLM_API_KEY" not in os.environ


async def test_run_engine_injects_corpus_params_per_scenario(tmp_path) -> None:
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    digest = hashlib.sha256(b"epub").hexdigest()
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        f"""
[[books]]
alias = "book"
path = "book.epub"
license = "public-domain"
sha256 = "{digest}"

[[books.probes]]
name = "core"
purposes = ["core"]
""",
        encoding="utf-8",
    )
    seen: list[str] = []

    async def script(context: ScenarioContext) -> None:
        assert context.params.corpus == book
        assert context.params.probe.name == "core"
        seen.append(str(context.params.corpus))

    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("with-corpus", script, corpus_purpose="core"))
    result = await RunEngine(registry, client_factory=FakeClient).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
            corpus_catalog_path=manifest,
        )
    )
    assert result.status == "passed"
    assert seen == [str(book)]
    manifest_body = json.loads((result.artifact_dir / "run_manifest.json").read_text())
    assert manifest_body["corpus"][0]["alias"] == "book"
    assert manifest_body["corpus"][0]["sha256"] == digest
    assert manifest_body["corpus"][0]["probe"]["name"] == "core"


async def test_run_engine_finalizes_when_cleanup_fails(tmp_path) -> None:
    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("passed", passing))
    result = await RunEngine(
        registry,
        client_factory=BrokenCloseClient,
        provider=BrokenCleanupProvider(),  # type: ignore[arg-type]
    ).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id="cleanup",
        )
    )
    assert result.status == "failed"
    assert (result.artifact_dir / "run_manifest.json").exists()
    failure = json.loads((result.artifact_dir / "failure/snapshot.json").read_text())
    assert "cleanup_errors" in failure["context"]


async def test_run_engine_scans_final_manifest_and_summary(tmp_path) -> None:
    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("leak", leaking_secret))
    result = await RunEngine(registry, client_factory=FakeClient).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id="leak",
        )
    )

    manifest = json.loads((result.artifact_dir / "run_manifest.json").read_text())
    summary = (result.artifact_dir / "reports/summary.md").read_text()
    assert result.status == "failed"
    assert manifest["safety_findings"]
    assert "top-secret" not in summary
    assert "***REDACTED***" in summary


async def test_run_engine_rejects_empty_selection(tmp_path) -> None:
    result = await RunEngine(ScenarioRegistry(), client_factory=FakeClient).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
        )
    )
    assert result.status == "failed"
    assert result.error == "no scenarios selected"


async def test_run_engine_fails_explicit_bad_verify_runtime(tmp_path) -> None:
    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("passed", passing))

    false_result = await RunEngine(
        registry,
        client_factory=RuntimeFalseClient,
    ).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id="runtime-false",
        )
    )
    assert false_result.status == "failed"
    assert "verify mode is not enabled" in false_result.error

    missing_base_url = await RunEngine(
        registry,
        client_factory=RuntimeMissingBaseURLClient,
    ).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id="runtime-missing-base-url",
        )
    )
    assert missing_base_url.status == "failed"
    assert "LLM base URL is not configured" in missing_base_url.error

    malformed = await RunEngine(
        registry,
        client_factory=RuntimeMalformedClient,
    ).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id="runtime-malformed",
        )
    )
    assert malformed.status == "failed"
    assert "missing verify_mode" in malformed.error


async def test_run_engine_imports_backend_agent_runs_when_local_missing(
    tmp_path,
) -> None:
    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("passed", passing))

    result = await RunEngine(
        registry,
        client_factory=AgentRunsClient,
    ).run(
        RunSpec(
            suite="core",
            profile=Profile(budget=Budget(max_cost_usd=1)),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id="backend-agent-runs",
        )
    )

    manifest = json.loads((result.artifact_dir / "run_manifest.json").read_text())
    lines = (
        result.artifact_dir / "evidence/agent_invocations.ndjson"
    ).read_text().splitlines()
    assert result.status == "passed"
    assert manifest["llm_call_count"] == 1
    assert json.loads(lines[0])["id"] == "backend-inv-1"


@pytest.mark.parametrize(
    "client_factory",
    [AgentRunsServerErrorClient, AgentRunsMalformedClient],
)
async def test_run_engine_reports_agent_import_failure_as_scenario_result(
    tmp_path,
    client_factory,
) -> None:
    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("passed", passing))

    result = await RunEngine(
        registry,
        client_factory=client_factory,
    ).run(
        RunSpec(
            suite="core",
            profile=Profile(),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id=f"agent-import-{client_factory.__name__}",
        )
    )

    failure = json.loads((result.artifact_dir / "failure/snapshot.json").read_text())
    assert result.status == "failed"
    assert result.scenarios[0]["id"] == "passed"
    assert result.scenarios[0]["status"] == "failed"
    assert result.scenarios[0]["error_type"]
    assert failure["context"]["id"] == "passed"
    assert failure["context"]["error_type"]


def test_budget_guard() -> None:
    with pytest.raises(BudgetExceeded, match="duration"):
        enforce_budget(Budget(max_duration_s=-1), EvidenceHub(), 0)
    hub = EvidenceHub()
    hub.record_invocation(
        AgentInvocation(
            id="inv-cost",
            agent="Reader",
            prompt_messages=[{"role": "user", "content": "x"}],
            response={},
            usage=TokenUsage(cost_usd=2.0),
            correlation=Correlation("run"),
        )
    )
    with pytest.raises(BudgetExceeded, match="cost"):
        enforce_budget(Budget(max_cost_usd=1), hub, 0)


def test_real_mode_evidence_gap_when_usage_missing() -> None:
    gaps = evidence_gaps(
        EvidenceHub(),
        Profile(llm_mode="real", budget=Budget(max_cost_usd=1)),
    )

    assert "real_mode_agent_invocation_usage_not_observed" in gaps
    assert required_evidence_gaps(Profile(llm_mode="real"), gaps) == gaps
    assert required_evidence_gaps(Profile(llm_mode="stub"), gaps) == []


def test_real_mode_evidence_gap_when_token_usage_missing() -> None:
    hub = EvidenceHub()
    hub.record_invocation(
        AgentInvocation(
            id="inv-zero-token",
            agent="Reader",
            prompt_messages=[],
            response={},
            usage=TokenUsage(source="provider", cost_usd=0.25),
            correlation=Correlation("run"),
        )
    )
    gaps = evidence_gaps(
        hub,
        Profile(llm_mode="real", budget=Budget(max_cost_usd=1)),
    )

    assert "real_mode_token_usage_not_observed: inv-zero-token" in gaps
    assert "real_mode_cost_usage_not_observed" not in gaps


def test_evidence_gap_reports_missing_scenario_and_step_correlation() -> None:
    hub = EvidenceHub()
    hub.record_invocation(
        AgentInvocation(
            id="inv",
            agent="Reader",
            prompt_messages=[],
            response={},
            usage=TokenUsage(input=1, source="provider"),
            correlation=Correlation("run"),
        )
    )
    gaps = evidence_gaps(hub)

    assert "agent_invocation_missing_scenario_correlation: inv" in gaps
    assert "agent_invocation_missing_step_correlation: inv" in gaps


async def test_real_mode_required_evidence_gap_fails_run(tmp_path) -> None:
    registry = ScenarioRegistry()
    registry.register(ScenarioDefinition("passed", passing))
    result = await RunEngine(
        registry,
        client_factory=FakeClient,
        provider=RealNoUsageProvider(),  # type: ignore[arg-type]
    ).run(
        RunSpec(
            suite="core",
            profile=Profile(
                llm_mode="real",
                budget=Budget(
                    max_calls=1,
                    max_tokens=1,
                    max_duration_s=10,
                    max_cost_usd=1,
                ),
            ),
            target_url="http://backend",
            artifact_root=tmp_path,
            run_id="real-no-usage",
        )
    )

    manifest = json.loads((result.artifact_dir / "run_manifest.json").read_text())
    failure = json.loads((result.artifact_dir / "failure/snapshot.json").read_text())
    assert result.status == "failed"
    assert manifest["status"] == "failed"
    assert "real_mode_agent_invocation_usage_not_observed" in manifest["evidence_gaps"]
    assert "evidence_gaps" in failure["context"]
