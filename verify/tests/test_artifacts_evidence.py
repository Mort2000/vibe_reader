from __future__ import annotations

import json

import pytest

from vibe_verify.artifact_store import ArtifactStore, redact_headers
from vibe_verify.evidence import EvidenceHub, LLMView, normalize_usage
from vibe_verify.models import (
    AgentInvocation,
    APIInteraction,
    Correlation,
    MetricPoint,
    SSEEvent,
    TokenUsage,
    ToolCall,
    UserInteraction,
    jsonable,
)


def invocation(agent: str = "Reader", *, id: str = "inv-1") -> AgentInvocation:
    return AgentInvocation(
        id=id,
        agent=agent,
        prompt_messages=[{"role": "user", "content": "hello"}],
        response={"content": "world"},
        usage=TokenUsage(input=3, output=2, cached_input=1, agent=agent),
        correlation=Correlation(run_id="r1", trace_id="t1"),
        tool_calls=[ToolCall(id="c1", name="emit", arguments={"x": 1})],
        duration_ms=12,
    )


def invocation_with_correlation(correlation: Correlation) -> AgentInvocation:
    base = invocation("Reader")
    return AgentInvocation(
        id=base.id,
        agent=base.agent,
        prompt_messages=base.prompt_messages,
        response=base.response,
        usage=base.usage,
        correlation=correlation,
        tool_calls=base.tool_calls,
    )


def test_jsonable_and_usage_total() -> None:
    value = jsonable(TokenUsage(input=2, output=3))
    assert value["input"] == 2
    assert TokenUsage(input=2, output=3).to_dict()["total"] == 5


def test_artifact_store_writes_manifest_audit_and_summary(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "run-1", audit_enabled=True)
    assert store.start().exists()
    store.append_ndjson("evidence/items.ndjson", [{"value": 1}])
    packet = store.write_audit_packet(invocation())
    transcript = store.write_llm_interaction_report([invocation()])
    store.write_manifest({"run_id": "run-1"})
    summary = store.write_summary(status="passed", scenarios=[], findings=[])

    assert json.loads(packet.read_text())["usage"]["total"] == 5
    assert "evidence_refs" in json.loads(packet.read_text())
    assert json.loads(packet.read_text())["evidence_refs"][
        "llm_interactions_report"
    ] == "audit/llm_interactions.md"
    assert (store.run_dir / "audit/prompts/inv-1.md").exists()
    transcript_text = transcript.read_text()
    assert "# LLM Interaction Audit Report" in transcript_text
    assert "## 1. `inv-1`" in transcript_text
    assert "hello" in transcript_text
    assert "world" in transcript_text
    assert "No scenarios executed" in summary.read_text()
    assert "Artifact Index" in summary.read_text()
    assert "audit/llm_interactions.md" in summary.read_text()
    with pytest.raises(FileExistsError):
        store.write_manifest({"run_id": "other"})


def test_artifact_store_rejects_reused_run_and_path_escape(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "run-1", audit_enabled=True)
    store.start()
    store.write_text("evidence/item.txt", "ok")

    with pytest.raises(FileExistsError):
        ArtifactStore(tmp_path, "run-1").start()
    with pytest.raises(ValueError, match="run_id"):
        ArtifactStore(tmp_path, "../bad")
    with pytest.raises(ValueError, match="within run directory"):
        store.write_text("../escape.txt", "bad")
    with pytest.raises(ValueError, match="agent invocation id"):
        store.write_audit_packet(invocation(id="../bad"))


def test_artifact_store_rejects_duplicate_audit_id(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "run-1", audit_enabled=True)
    store.start()
    store.write_audit_packet(invocation())
    with pytest.raises(FileExistsError):
        store.write_audit_packet(invocation())


def test_artifact_store_requires_explicit_audit_and_scans_secrets(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    store.start()
    with pytest.raises(RuntimeError):
        store.write_audit_packet(invocation())
    with pytest.raises(RuntimeError):
        store.write_llm_interaction_report([invocation()])
    store.write_text("evidence/leak.md", "api_key: sk-1234567890abcdef")
    findings = store.scan_secrets()
    assert findings and findings[0].path == "evidence/leak.md"


def test_redact_headers() -> None:
    assert redact_headers(
        {
            "Authorization": "Bearer secret",
            "Proxy-Authorization": "Basic secret",
            "Set-Cookie": "sid=secret",
            "OpenAI-API-Key": "secret",
            "X-Test": "ok",
        }
    ) == {
        "Authorization": "***REDACTED***",
        "Proxy-Authorization": "***REDACTED***",
        "Set-Cookie": "***REDACTED***",
        "OpenAI-API-Key": "***REDACTED***",
        "X-Test": "ok",
    }


def test_evidence_hub_persists_and_queries(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "run", audit_enabled=True)
    store.start()
    hub = EvidenceHub(store=store, audit_enabled=True)
    correlation = Correlation(run_id="run")
    hub.record_api(APIInteraction("GET", "/api/health", 200, 1, correlation))
    hub.record_sse(SSEEvent("chat.delta", {"delta": "secret output"}, correlation))
    hub.record_metric(MetricPoint("latency", 1, "ms", correlation))
    hub.record_user(UserInteraction("read", {}, correlation))
    hub.record_stub_journal({"request": "ok"})
    hub.record_otel({"trace_id": "trace"})

    with hub.observe() as window:
        hub.record_invocation(invocation("Reader"))
    hub.record_invocation(invocation("Reader", id="inv-2"))

    assert len(hub.calls(agent="Reader", window=window)) == 1
    assert (store.run_dir / "evidence/api.ndjson").exists()
    assert "secret output" not in (store.run_dir / "evidence/sse.ndjson").read_text()
    assert (store.run_dir / "audit/agent_interactions/inv-1.json").exists()


def test_evidence_sanitizes_direct_api_and_stub_journal(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "run")
    store.start()
    hub = EvidenceHub(store=store)
    hub.record_api(
        APIInteraction(
            "POST",
            "/api/chat",
            200,
            1,
            Correlation("run"),
            request_headers={"Authorization": "Bearer secret"},
            request_body={"user_msg": "private question"},
            response_body={"ai_msg": "private answer"},
        )
    )
    hub.record_stub_journal(
        {
            "agent": "Chat",
            "request": {"messages": [{"content": "private prompt"}]},
            "response": {"choices": [{"message": {"content": "private output"}}]},
            "status": 200,
            "profile_hash": "sha256:x",
            "seed": 1,
            "model": "m",
            "stub_version": "v",
        }
    )
    hub.record_user(
        UserInteraction(
            "chat",
            {"message": "private user question", "paragraph_idx": 7},
            Correlation("run"),
            outcome={"text": "private model answer", "tokens_out": 3},
        )
    )

    assert hub.api_interactions[0].request_headers["Authorization"] == "***REDACTED***"
    assert hub.api_interactions[0].request_body["keys"] == ["user_msg"]
    api_text = (store.run_dir / "evidence/api.ndjson").read_text()
    stub_text = (store.run_dir / "stub/journal.ndjson").read_text()
    user_text = (store.run_dir / "evidence/user_interactions.ndjson").read_text()
    assert "private question" not in api_text
    assert "private answer" not in api_text
    assert '"keys": ["user_msg"]' in api_text
    assert "private prompt" not in stub_text
    assert "private output" not in stub_text
    assert "private user question" not in user_text
    assert "private model answer" not in user_text
    assert '"paragraph_idx": 7' in user_text


def test_audit_mode_persists_full_user_interactions(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "run", audit_enabled=True)
    store.start()
    hub = EvidenceHub(store=store, audit_enabled=True)
    hub.record_user(
        UserInteraction(
            "chat",
            {"message": "full user question"},
            Correlation("run"),
            outcome={"text": "full model answer"},
        )
    )

    ordinary = (store.run_dir / "evidence/user_interactions.ndjson").read_text()
    audit = (store.run_dir / "audit/user_interactions.ndjson").read_text()
    assert "full user question" not in ordinary
    assert "full model answer" not in ordinary
    assert "full user question" in audit
    assert "full model answer" in audit


def test_otel_records_are_sanitized_outside_audit(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "run")
    store.start()
    hub = EvidenceHub(store=store)

    hub.record_otel(
        {
            "name": "agent.run",
            "trace_id": "trace",
            "attributes": {"prompt": "private prompt", "api_key": "secret"},
        }
    )

    text = (store.run_dir / "evidence/otel.ndjson").read_text()
    assert "private prompt" not in text
    assert "api_key" not in text
    assert "trace" in text


def test_llm_view_expectations_and_usage() -> None:
    hub = EvidenceHub()
    llm = LLMView(hub)
    with llm.expect_calls(max=1):
        hub.record_invocation(invocation())
    assert llm.last_call().agent == "Reader"
    assert llm.total_usage().total == 5
    with pytest.raises(AssertionError, match="at most"), llm.expect_calls(max=0):
        hub.record_invocation(invocation())
    with pytest.raises(AssertionError, match="no LLM calls"):
        LLMView(EvidenceHub()).last_call()


def test_llm_view_filters_by_scenario_and_step() -> None:
    hub = EvidenceHub()
    hub.record_invocation(
        invocation_with_correlation(Correlation("run", scenario_id="s1", step_id="a"))
    )
    hub.record_invocation(
        invocation_with_correlation(Correlation("run", scenario_id="s2", step_id="b"))
    )
    llm = LLMView(hub)
    assert len(llm.calls(scenario_id="s1")) == 1
    assert len(llm.calls(step_id="b")) == 1


def test_normalize_openai_usage() -> None:
    usage = normalize_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
        source="provider",
        agent="Chat",
    )
    assert usage.to_dict() == {
        "input": 10,
        "output": 4,
        "cached_input": 3,
        "cost_usd": 0.0,
        "source": "provider",
        "agent": "Chat",
        "model": "",
        "total": 14,
    }
