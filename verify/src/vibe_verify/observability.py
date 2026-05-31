"""Read-only backend observability adapter for verify-only evidence."""

from __future__ import annotations

from typing import Any

from .driver import BookFacade, TargetClient, parse_items, require_success, unwrap
from .evidence import EvidenceHub, normalize_usage
from .models import (
    AgentInvocation,
    Correlation,
    ToolCall,
    optional_float,
    optional_int,
)


class ObservabilityUnavailable(RuntimeError):
    """Raised when optional verify observability endpoints are not exposed."""


class BackendObservability:
    """Query verify-mode backend evidence without driving product state."""

    def __init__(self, client: TargetClient):
        self.client = client

    async def runtime(self) -> dict[str, Any]:
        response = await self.client.request("GET", "/api/verify/runtime")
        require_success(response)
        body = unwrap(response.body)
        if not isinstance(body, dict):
            raise TypeError("verify runtime response must be an object")
        return body

    async def runtime_if_available(self) -> dict[str, Any] | None:
        response = await self.client.request("GET", "/api/verify/runtime")
        if response.status_code in {404, 405}:
            return None
        require_success(response)
        body = unwrap(response.body)
        if not isinstance(body, dict):
            raise TypeError("verify runtime response must be an object")
        if "verify_mode" not in body:
            raise TypeError("verify runtime response missing verify_mode")
        return body

    async def list_jobs(
        self,
        *,
        book: BookFacade | None = None,
        job_type: str | None = None,
        status: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "run_id": self.client.correlation.run_id if run_id is None else run_id,
            "limit": limit,
        }
        if book is not None:
            params["book_id"] = book.id
            params["chapter_idx"] = book.chapter_idx
        if job_type is not None:
            params["job_type"] = job_type
        if status is not None:
            params["status"] = status
        response = await self.client.request("GET", "/api/verify/jobs", params=params)
        require_success(response)
        return parse_items(unwrap(response.body), preferred_key="jobs")

    async def list_agent_runs(
        self,
        *,
        scenario_id: str | None = None,
        include_interaction: bool = True,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "run_id": self.client.correlation.run_id,
            "include_interaction": include_interaction,
        }
        if scenario_id is not None:
            params["scenario_id"] = scenario_id
        response = await self.client.request(
            "GET", "/api/verify/agent-runs", params=params
        )
        if response.status_code in {404, 405}:
            raise ObservabilityUnavailable("backend agent-run endpoint unavailable")
        require_success(response)
        return parse_items(unwrap(response.body), preferred_key="agent_runs")

    async def metrics(self, *, scenario_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"run_id": self.client.correlation.run_id}
        if scenario_id is not None:
            params["scenario_id"] = scenario_id
        response = await self.client.request(
            "GET", "/api/verify/metrics", params=params
        )
        require_success(response)
        body = unwrap(response.body)
        if not isinstance(body, dict):
            raise TypeError("verify metrics response must be an object")
        return body

    async def collect_agent_invocations(
        self,
        evidence: EvidenceHub,
        *,
        scenario_id: str | None = None,
    ) -> int:
        """Import backend-recorded Agent runs into the local evidence view."""
        rows = await self.list_agent_runs(
            scenario_id=scenario_id,
            include_interaction=True,
        )
        existing = {item.id for item in evidence.invocations}
        added = 0
        for row in rows:
            invocation = agent_invocation_from_backend_row(row)
            if invocation.id in existing:
                continue
            evidence.record_invocation(invocation)
            existing.add(invocation.id)
            added += 1
        return added

    async def collect_agent_invocations_if_available(
        self,
        evidence: EvidenceHub,
        *,
        scenario_id: str | None = None,
    ) -> int:
        try:
            return await self.collect_agent_invocations(
                evidence,
                scenario_id=scenario_id,
            )
        except ObservabilityUnavailable:
            return 0


def agent_invocation_from_backend_row(row: dict[str, Any]) -> AgentInvocation:
    raw_interaction = row.get("interaction")
    interaction = raw_interaction if isinstance(raw_interaction, dict) else {}
    raw_usage = interaction.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    prompt_messages = interaction.get("prompt_messages") or []
    tool_events = interaction.get("tool_events") or []
    agent = str(row.get("agent_name") or interaction.get("agent") or "")
    context_hash = str(
        row.get("context_hash") or interaction.get("context_hash") or ""
    )
    duration_ms = optional_float(
        first_present_optional(row.get("duration_ms"), interaction.get("duration_ms"))
    )
    return AgentInvocation(
        id=str(row.get("invocation_id") or row.get("trace_id") or "backend_agent_run"),
        agent=agent,
        prompt_messages=(
            list(prompt_messages) if isinstance(prompt_messages, list) else []
        ),
        response=interaction,
        usage=normalize_usage(
            backend_usage_payload(row, interaction, usage),
            source=str(
                usage.get("source")
                or interaction.get("usage_source")
                or row.get("usage_source")
                or "framework"
            ),
            agent=agent,
            model=str(interaction.get("model") or row.get("model") or ""),
        ),
        correlation=Correlation(
            run_id=str(row.get("verify_run_id") or interaction.get("run_id") or ""),
            scenario_id=str(
                row.get("verify_scenario_id") or interaction.get("scenario_id") or ""
            ),
            step_id=str(row.get("verify_step_id") or interaction.get("step_id") or ""),
            trace_id=str(row.get("trace_id") or interaction.get("trace_id") or ""),
            book_id=optional_int(
                first_present_optional(row.get("book_id"), interaction.get("book_id"))
            ),
            chapter_idx=optional_int(
                first_present_optional(
                    row.get("chapter_idx"), interaction.get("chapter_idx")
                )
            ),
            window_id=optional_int(
                first_present_optional(
                    row.get("window_id"), interaction.get("window_id")
                )
            ),
            job_id=optional_int(
                first_present_optional(row.get("job_id"), interaction.get("job_id"))
            ),
            agent_invocation_id=str(row.get("invocation_id") or ""),
            context_hash=context_hash,
        ),
        tool_calls=[
            ToolCall(
                id=str(item.get("tool_call_id") or ""),
                name=str(item.get("tool_name") or ""),
                arguments=(
                    item.get("arguments")
                    if isinstance(item.get("arguments"), dict)
                    else {}
                ),
            )
            for item in tool_events
            if isinstance(item, dict)
        ],
        tool_results=[
            item.get("tool_result")
            for item in tool_events
            if isinstance(item, dict) and isinstance(item.get("tool_result"), dict)
        ],
        duration_ms=duration_ms,
    )


def backend_usage_payload(
    row: dict[str, Any],
    interaction: dict[str, Any],
    usage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "input": first_present(
            row.get("input_tokens"),
            interaction.get("input_tokens"),
            usage.get("input_tokens"),
            usage.get("input"),
            usage.get("prompt_tokens"),
        ),
        "output": first_present(
            row.get("output_tokens"),
            interaction.get("output_tokens"),
            usage.get("output_tokens"),
            usage.get("output"),
            usage.get("completion_tokens"),
        ),
        "cached_input": first_present(
            row.get("cached_input_tokens"),
            interaction.get("cached_input_tokens"),
            usage.get("cached_input_tokens"),
            usage.get("cached_input"),
        ),
        "cost_usd": first_present(
            row.get("cost_usd"),
            interaction.get("cost_usd"),
            usage.get("cost_usd"),
            usage.get("cost"),
        ),
    }


def first_present(*values: Any) -> Any:
    value = first_present_optional(*values)
    return 0 if value is None else value


def first_present_optional(*values: Any) -> Any:
    for value in values:
        if value not in ("", None):
            return value
    return None
