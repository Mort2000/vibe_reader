from __future__ import annotations

import httpx
import pytest

from vibe_verify.evidence import EvidenceHub
from vibe_verify.models import Correlation
from vibe_verify.provider import (
    GenericStubRouter,
    ProviderHarness,
    StubProfile,
    StubSidecar,
    classify_prompt,
    classify_request,
    parse_comment_targets,
    update_default_correlation,
)
from vibe_verify.runner import Budget


def request(prompt: str, *, stream: bool = False) -> dict:
    return {
        "model": "test",
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }


def tool_request(prompt: str, tool_name: str) -> dict:
    payload = request(prompt)
    payload["tools"] = [{"type": "function", "function": {"name": tool_name}}]
    return payload


def message(reply) -> dict:
    return reply.body["choices"][0]["message"]


def tool_followup(prompt: str, reply) -> dict:
    calls = message(reply).get("tool_calls") or []
    return {
        "model": "test",
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": None, "tool_calls": calls},
            *[
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": "accepted",
                }
                for call in calls
            ],
        ],
    }


def test_parse_comment_targets_and_classifier() -> None:
    prompt = "comment_target_paragraphs = [3..=1, 8, 8]"
    assert parse_comment_targets(prompt) == [1, 2, 3, 8]
    assert classify_prompt(prompt) == "ParagraphCommentAgent"
    assert (
        classify_prompt("ContextCompactionAgent context compaction")
        == "ContextCompactionAgent"
    )
    assert classify_prompt("ReadingChatAgent mode = chat") == "ReadingChatAgent"
    assert classify_prompt("Reply with exactly: ok") == "S0Ping"
    assert classify_prompt("other") == "Unknown"
    assert (
        classify_request(
            tool_request(
                "<SOURCE_ORIGINAL_CHUNK>...</SOURCE_ORIGINAL_CHUNK>",
                "emit_chapter_compressed_summary",
            )
        )
        == "ContextCompactionAgent"
    )


def test_router_known_requests_and_journal() -> None:
    evidence = EvidenceHub()
    router = GenericStubRouter(evidence=evidence)
    ping = router.route(request("Reply with exactly: ok"))
    comments = router.route(request("comment_target_paragraphs = [1..=4]"))
    compact = router.route(request("ContextCompactionAgent context compaction"))
    chat = router.route(request("ReadingChatAgent mode = chat"))
    stream = router.route(request("ReadingChatAgent mode = chat", stream=True))

    assert message(ping)["content"] == "ok"
    assert len(message(comments)["tool_calls"]) == 3
    assert message(compact)["tool_calls"][0]["function"]["name"] == (
        "emit_chapter_compressed_summary"
    )
    assert "[chat]" in message(chat)["content"]
    assert stream.stream and stream.chunks
    assert len(router.journal) == 5
    assert router.journal[0]["profile_hash"].startswith("sha256:")
    assert router.journal[0]["seed"] == 20260522
    assert router.journal[0]["model"] == "vibe-reader-stub"
    assert len(evidence.invocations) == 5


def test_router_rejects_comment_contract_without_targets() -> None:
    reply = GenericStubRouter().route(
        tool_request("missing explicit target list", "emit_comment")
    )

    assert reply.status == 422
    assert reply.body["error"]["code"] == "missing_comment_targets"


def test_stub_invocation_carries_request_correlation() -> None:
    evidence = EvidenceHub()
    router = GenericStubRouter(evidence=evidence)
    router.route(
        {
            **request("comment_target_paragraphs = [1]"),
            "metadata": {
                "run_id": "run",
                "scenario_id": "scenario",
                "step_id": "step",
                "trace_id": "trace",
                "book_id": "7",
            },
        }
    )

    assert evidence.invocations[0].correlation.run_id == "run"
    assert evidence.invocations[0].correlation.scenario_id == "scenario"
    assert evidence.invocations[0].correlation.book_id == 7


def test_stub_invocation_uses_updated_default_correlation() -> None:
    evidence = EvidenceHub()
    harness = ProviderHarness()
    session = harness.prepare(
        mode="stub",
        evidence=evidence,
        default_correlation=Correlation(run_id="run"),
    )
    try:
        update_default_correlation(
            session,
            Correlation(run_id="run", scenario_id="scenario", step_id="step"),
        )
        assert session.sidecar is not None
        session.sidecar.router.route(request("comment_target_paragraphs = [1]"))
    finally:
        harness.cleanup(session)

    assert evidence.invocations[0].correlation.run_id == "run"
    assert evidence.invocations[0].correlation.scenario_id == "scenario"
    assert evidence.invocations[0].correlation.step_id == "step"


def test_router_finishes_after_tool_result() -> None:
    router = GenericStubRouter()
    comments = router.route(request("comment_target_paragraphs = [1..=4]"))
    comment_done = router.route(
        tool_followup("comment_target_paragraphs = [1..=4]", comments)
    )
    compact = router.route(request("ContextCompactionAgent context compaction"))
    compact_done = router.route(
        tool_followup("ContextCompactionAgent context compaction", compact)
    )

    assert "tool_calls" not in message(comment_done)
    assert "comments accepted" in message(comment_done)["content"]
    assert "compaction accepted" in message(compact_done)["content"]


def test_stub_ids_are_deterministic_for_same_request() -> None:
    payload = request("comment_target_paragraphs = [1..=2]")
    first = GenericStubRouter().route(payload).body
    second = GenericStubRouter().route(payload).body
    assert first == second


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("no_call", "content"),
        ("invalid_tool_args", "tool_calls"),
        ("timeout", "error"),
    ],
)
def test_router_fault_profiles(fault: str, expected: str) -> None:
    router = GenericStubRouter(StubProfile(fault=fault, timeout_delay_s=0.01))
    reply = router.route(request("comment_target_paragraphs = [1]"))
    if expected == "error":
        assert reply.status == 504
    else:
        assert expected in message(reply)


def test_provider_error_once_and_unknown_strict_failure() -> None:
    router = GenericStubRouter(StubProfile(fault="provider_error"))
    assert router.route(request("Reply with exactly: ok")).status == 429
    assert router.route(request("Reply with exactly: ok")).status == 200
    assert GenericStubRouter().route(request("unknown")).status == 422


def test_timeout_fault_can_trigger_client_timeout() -> None:
    router = GenericStubRouter(StubProfile(fault="timeout", timeout_delay_s=0.1))
    with StubSidecar(router) as sidecar, pytest.raises(httpx.ReadTimeout):
        httpx.post(
            sidecar.base_url + "/chat/completions",
            json=request("Reply with exactly: ok"),
            timeout=0.01,
        )


def test_sidecar_serves_health_completion_stream_and_journal() -> None:
    with StubSidecar() as sidecar:
        assert httpx.get(sidecar.base_url.removesuffix("/v1") + "/health").json() == {
            "status": "ok"
        }
        response = httpx.post(
            sidecar.base_url + "/chat/completions",
            json=request("Reply with exactly: ok"),
        )
        assert response.json()["choices"][0]["message"]["content"] == "ok"
        with httpx.stream(
            "POST",
            sidecar.base_url + "/chat/completions",
            json=request("ReadingChatAgent mode = chat", stream=True),
        ) as stream:
            assert "data: [DONE]" in stream.read().decode()
        assert (
            len(
                httpx.get(sidecar.base_url.removesuffix("/v1") + "/journal").json()[
                    "records"
                ]
            )
            == 2
        )


def test_provider_harness_modes_and_cleanup() -> None:
    harness = ProviderHarness()
    stub = harness.prepare(mode="stub")
    assert stub.backend_env["VIBE_READER_LLM_BASE_URL"].endswith("/v1")
    assert stub.usage_source == "estimate"
    harness.cleanup(stub)
    with pytest.raises(RuntimeError):
        assert stub.sidecar is not None
        _ = stub.sidecar.base_url

    real = harness.prepare(
        mode="real",
        real_base_url="https://provider.example/v1",
        real_api_key="secret",
        real_model="model",
        real_budget=Budget(max_cost_usd=1),
    )
    assert real.usage_source == "provider"
    with pytest.raises(ValueError):
        harness.prepare(mode="real")
    with pytest.raises(ValueError, match="budget"):
        harness.prepare(
            mode="real",
            real_base_url="https://provider.example/v1",
            real_api_key="secret",
            real_model="model",
        )
    with pytest.raises(ValueError):
        harness.prepare(mode="other")
