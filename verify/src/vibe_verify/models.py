"""Stable data models shared across verify modules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from typing import Any

USAGE_SOURCES = frozenset({"provider", "framework", "estimate", "mixed"})


def jsonable(value: Any) -> Any:
    """Convert dataclasses and nested containers into JSON-compatible values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class Correlation:
    """Identifiers used to connect a user action with backend evidence."""

    run_id: str
    scenario_id: str = ""
    step_id: str = ""
    request_id: str = ""
    trace_id: str = ""
    book_id: int | None = None
    chapter_idx: int | None = None
    window_id: int | None = None
    job_id: int | None = None
    agent_invocation_id: str = ""
    context_hash: str = ""
    prompt_hash: str = ""


def optional_int(value: Any) -> int | None:
    if value in ("", None):
        return None
    return int(value)


def optional_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    return float(value)


def merge_correlation(base: Correlation, data: Mapping[str, Any]) -> Correlation:
    """Merge backend metadata into a Correlation with stable field types."""
    updates: dict[str, Any] = {}
    for field_name in (
        "run_id",
        "scenario_id",
        "step_id",
        "request_id",
        "trace_id",
        "agent_invocation_id",
        "context_hash",
        "prompt_hash",
    ):
        value = data.get(field_name)
        if value not in ("", None):
            updates[field_name] = str(value)
    for field_name in ("book_id", "chapter_idx", "window_id", "job_id"):
        value = optional_int(data.get(field_name))
        if value is not None:
            updates[field_name] = value
    return replace(base, **updates)


@dataclass(frozen=True)
class TokenUsage:
    """Normalized LLM usage with an explicit source."""

    input: int = 0
    output: int = 0
    cached_input: int = 0
    cost_usd: float = 0.0
    source: str = "estimate"
    agent: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if self.source not in USAGE_SOURCES:
            allowed = ", ".join(sorted(USAGE_SOURCES))
            raise ValueError(
                f"unsupported token usage source: {self.source!r}; allowed={allowed}"
            )

    @property
    def total(self) -> int:
        return self.input + self.output

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["total"] = self.total
        return data


@dataclass(frozen=True)
class ToolCall:
    """Model tool invocation normalized across provider protocols."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class APIInteraction:
    """Sanitized API request and response evidence."""

    method: str
    path: str
    status_code: int
    duration_ms: float
    correlation: Correlation
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: Any = None
    response_body: Any = None
    error: str = ""


@dataclass(frozen=True)
class SSEEvent:
    """One parsed SSE event."""

    event_type: str
    data: dict[str, Any]
    correlation: Correlation


@dataclass(frozen=True)
class MetricPoint:
    """One normalized metric observation."""

    name: str
    value: float
    unit: str
    correlation: Correlation
    tags: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentInvocation:
    """Auditable model interaction for one business agent invocation."""

    id: str
    agent: str
    prompt_messages: list[dict[str, Any]]
    response: Any
    usage: TokenUsage
    correlation: Correlation
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    ttft_ms: float | None = None
    duration_ms: float | None = None
    retries: int = 0
    error: str = ""
    thinking: str | None = None
    thinking_unavailable_reason: str = ""

    @property
    def prompt(self) -> str:
        return "\n".join(
            str(message.get("content", "")) for message in self.prompt_messages
        )


@dataclass(frozen=True)
class UserInteraction:
    """Recorded user-facing action and its observable outcome."""

    action: str
    arguments: dict[str, Any]
    correlation: Correlation
    duration_ms: float = 0.0
    outcome: Any = None
