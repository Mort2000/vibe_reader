"""Read-only evidence collection and model observation views."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from .artifact_store import ArtifactStore, redact_headers
from .models import (
    AgentInvocation,
    APIInteraction,
    MetricPoint,
    SSEEvent,
    TokenUsage,
    UserInteraction,
    jsonable,
)


@dataclass
class ObservationWindow:
    """A closed invocation range for explicit observation intervals."""

    hub: EvidenceHub
    invocation_start: int
    invocation_end: int | None = None

    @property
    def calls(self) -> list[AgentInvocation]:
        return self.hub.invocations[self.invocation_start : self.invocation_end]

    def close(self) -> None:
        self.invocation_end = len(self.hub.invocations)


class EvidenceHub:
    """Collect append-only evidence without controlling product behavior."""

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        audit_enabled: bool = False,
    ):
        self.store = store
        self.audit_enabled = audit_enabled
        self.api_interactions: list[APIInteraction] = []
        self.sse_events: list[SSEEvent] = []
        self.metrics: list[MetricPoint] = []
        self.invocations: list[AgentInvocation] = []
        self.user_interactions: list[UserInteraction] = []
        self.stub_journal: list[dict[str, Any]] = []
        self.otel_records: list[dict[str, Any]] = []

    def record_api(self, interaction: APIInteraction) -> None:
        sanitized = sanitized_api_interaction(interaction)
        self.api_interactions.append(sanitized)
        self._append("evidence/api.ndjson", api_interaction_summary(sanitized))

    def record_sse(self, event: SSEEvent) -> None:
        self.sse_events.append(event)
        self._append("evidence/sse.ndjson", sse_event_summary(event))

    def record_metric(self, metric: MetricPoint) -> None:
        self.metrics.append(metric)
        self._append("evidence/metrics.ndjson", metric)

    def record_user(self, interaction: UserInteraction) -> None:
        self.user_interactions.append(interaction)
        self._append(
            "evidence/user_interactions.ndjson",
            user_interaction_summary(interaction),
        )
        if self.store is not None and self.audit_enabled:
            self.store.append_ndjson("audit/user_interactions.ndjson", [interaction])

    def record_invocation(self, invocation: AgentInvocation) -> None:
        self.invocations.append(invocation)
        self._append(
            "evidence/agent_invocations.ndjson", invocation_summary(invocation)
        )
        if self.store is not None and self.audit_enabled:
            self.store.write_audit_packet(invocation)

    def record_stub_journal(self, record: dict[str, Any]) -> None:
        self.stub_journal.append(record)
        self._append("stub/journal.ndjson", stub_journal_summary(record))
        if self.store is not None and self.audit_enabled:
            self.store.append_ndjson("audit/stub_journal.ndjson", [record])

    def record_otel(self, record: dict[str, Any]) -> None:
        self.otel_records.append(record)
        self._append("evidence/otel.ndjson", otel_record_summary(record))
        if self.store is not None and self.audit_enabled:
            self.store.append_ndjson("audit/otel.ndjson", [record])

    def calls(
        self,
        *,
        agent: str | None = None,
        scenario_id: str | None = None,
        step_id: str | None = None,
        window: ObservationWindow | None = None,
    ) -> list[AgentInvocation]:
        items = window.calls if window is not None else self.invocations
        return [
            item
            for item in items
            if (agent is None or item.agent == agent)
            and (scenario_id is None or item.correlation.scenario_id == scenario_id)
            and (step_id is None or item.correlation.step_id == step_id)
        ]

    @contextmanager
    def observe(self) -> Iterator[ObservationWindow]:
        window = ObservationWindow(self, len(self.invocations))
        try:
            yield window
        finally:
            window.close()

    def _append(self, relative: str, record: Any) -> None:
        if self.store is not None:
            self.store.append_ndjson(relative, [record])


class LLMView:
    """Scenario-facing read-only model observation helper."""

    def __init__(self, hub: EvidenceHub):
        self.hub = hub

    def calls(
        self,
        agent: str | None = None,
        *,
        scenario_id: str | None = None,
        step_id: str | None = None,
        window: ObservationWindow | None = None,
    ) -> list[AgentInvocation]:
        return self.hub.calls(
            agent=agent,
            scenario_id=scenario_id,
            step_id=step_id,
            window=window,
        )

    def last_call(self, agent: str | None = None) -> AgentInvocation:
        calls = self.calls(agent)
        if not calls:
            raise AssertionError(f"no LLM calls recorded for agent={agent!r}")
        return calls[-1]

    @contextmanager
    def expect_calls(
        self,
        *,
        agent: str | None = None,
        min: int = 0,
        max: int | None = None,
    ) -> Iterator[ObservationWindow]:
        with self.hub.observe() as window:
            yield window
        count = len(self.calls(agent, window=window))
        if count < min:
            raise AssertionError(f"expected at least {min} LLM calls, got {count}")
        if max is not None and count > max:
            raise AssertionError(f"expected at most {max} LLM calls, got {count}")

    def total_usage(self, agent: str | None = None) -> TokenUsage:
        calls = self.calls(agent)
        sources = {call.usage.source for call in calls}
        source = sources.pop() if len(sources) == 1 else "mixed"
        return TokenUsage(
            input=sum(call.usage.input for call in calls),
            output=sum(call.usage.output for call in calls),
            cached_input=sum(call.usage.cached_input for call in calls),
            cost_usd=sum(call.usage.cost_usd for call in calls),
            source=source,
            agent=agent or "*",
        )


def normalize_usage(
    usage: dict[str, Any] | None,
    *,
    source: str,
    agent: str = "",
    model: str = "",
) -> TokenUsage:
    """Normalize OpenAI-style and verify-style usage payloads."""
    data = usage or {}
    details = data.get("prompt_tokens_details") or {}
    return TokenUsage(
        input=int(data.get("input", data.get("prompt_tokens", 0)) or 0),
        output=int(data.get("output", data.get("completion_tokens", 0)) or 0),
        cached_input=int(
            data.get("cached_input", details.get("cached_tokens", 0)) or 0
        ),
        cost_usd=float(data.get("cost_usd", data.get("cost", 0.0)) or 0.0),
        source=source,
        agent=agent,
        model=model,
    )


def invocation_summary(invocation: AgentInvocation) -> dict[str, Any]:
    """Keep high-volume content out of ordinary evidence."""
    return {
        "id": invocation.id,
        "agent": invocation.agent,
        "usage": invocation.usage.to_dict(),
        "correlation": jsonable(invocation.correlation),
        "tool_call_count": len(invocation.tool_calls),
        "ttft_ms": invocation.ttft_ms,
        "duration_ms": invocation.duration_ms,
        "retries": invocation.retries,
        "error": invocation.error,
    }


def api_interaction_summary(interaction: APIInteraction) -> dict[str, Any]:
    """Ordinary API evidence keeps metadata, hashes, and safe scalar fields."""
    return {
        "method": interaction.method,
        "path": interaction.path,
        "status_code": interaction.status_code,
        "duration_ms": interaction.duration_ms,
        "correlation": jsonable(interaction.correlation),
        "request_headers": redact_headers(interaction.request_headers),
        "request_body": payload_summary(interaction.request_body),
        "response_body": payload_summary(interaction.response_body),
        "error": interaction.error[:200],
    }


def sanitized_api_interaction(interaction: APIInteraction) -> APIInteraction:
    return replace(
        interaction,
        request_headers=redact_headers(interaction.request_headers),
        request_body=safe_payload_summary(interaction.request_body),
        response_body=safe_payload_summary(interaction.response_body),
    )


def sse_event_summary(event: SSEEvent) -> dict[str, Any]:
    """Ordinary SSE evidence excludes streamed text and completion payloads."""
    safe = pick_safe_fields(event.data)
    return {
        "event_type": event.event_type,
        "data": safe,
        "payload": safe_payload_summary(event.data),
        "correlation": jsonable(event.correlation),
    }


def stub_journal_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent": record.get("agent", ""),
        "status": record.get("status"),
        "stream": bool(record.get("stream", False)),
        "usage_source": record.get("usage_source", ""),
        "duration_ms": record.get("duration_ms"),
        "profile": record.get("profile", ""),
        "profile_hash": record.get("profile_hash", ""),
        "seed": record.get("seed"),
        "model": record.get("model", ""),
        "stub_version": record.get("stub_version", ""),
        "request": safe_payload_summary(record.get("request")),
        "response": safe_payload_summary(record.get("response")),
    }


def user_interaction_summary(interaction: UserInteraction) -> dict[str, Any]:
    """Ordinary user evidence keeps action metadata without user text."""
    return {
        "action": interaction.action,
        "arguments": safe_payload_summary(interaction.arguments),
        "correlation": jsonable(interaction.correlation),
        "duration_ms": interaction.duration_ms,
        "outcome": safe_payload_summary(interaction.outcome),
    }


def otel_record_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Ordinary OTEL evidence keeps identifiers and hashes, not attributes."""
    safe = pick_safe_fields(record)
    payload = safe_payload_summary(record)
    return {
        "name": record.get("name", record.get("span_name", "")),
        "kind": record.get("kind", record.get("type", "")),
        "timestamp": record.get("timestamp", record.get("time_unix_nano", "")),
        "severity": record.get("severity_text", record.get("level", "")),
        "data": safe,
        "payload": payload,
    }


def pick_safe_fields(data: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "event_id",
        "trace_id",
        "request_id",
        "book_id",
        "chapter_idx",
        "paragraph_idx",
        "window_id",
        "job_id",
        "agent_invocation_id",
        "tokens_in",
        "tokens_out",
        "turn_id",
        "session_id",
        "code",
        "error_code",
    )
    return {key: data[key] for key in keys if key in data}


def safe_payload_summary(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    raw = jsonable(value)
    encoded = json.dumps(raw, sort_keys=True, default=str, ensure_ascii=False).encode()
    summary: dict[str, Any] = {
        "type": type(value).__name__,
        "size_bytes": len(encoded),
        "hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }
    if isinstance(raw, dict):
        summary["keys"] = sorted(str(key) for key in raw)
        for key in (
            "ok",
            "status",
            "code",
            "trace_id",
            "request_id",
            "book_id",
            "chapter_idx",
            "paragraph_idx",
            "window_id",
            "job_id",
            "turn_id",
            "session_id",
            "tokens_in",
            "tokens_out",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        ):
            if key in raw and isinstance(
                raw[key], str | int | float | bool | type(None)
            ):
                summary[key] = raw[key]
    elif isinstance(raw, list | tuple | str | bytes):
        summary["length"] = len(raw)
    return summary


def payload_summary(value: Any) -> dict[str, Any] | None:
    if is_payload_summary(value):
        return value
    return safe_payload_summary(value)


def is_payload_summary(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("type"), str)
        and isinstance(value.get("size_bytes"), int)
        and isinstance(value.get("hash"), str)
    )
