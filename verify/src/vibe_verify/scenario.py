"""Scenario registry and ordinary Python user-script execution."""

from __future__ import annotations

import inspect
import traceback
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .driver import AppFacade, UserFacade
from .evidence import LLMView
from .observability import BackendObservability

ScenarioScript = Callable[["ScenarioContext"], Awaitable[None] | None]
ScenarioCheck = Callable[["ScenarioContext"], Awaitable[None] | None]


@dataclass(frozen=True)
class ScenarioParameters:
    """Immutable scenario-local parameters exposed as ``param`` in scripts."""

    corpus: Path | None = None
    probe: Any = None
    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass(frozen=True)
class ScenarioDefinition:
    """Registered user script and its selection metadata."""

    id: str
    script: ScenarioScript
    suites: frozenset[str] = frozenset({"core"})
    profiles: frozenset[str] = frozenset()
    corpus_purpose: str = ""
    description: str = ""
    post_checks: tuple[ScenarioCheck, ...] = ()


@dataclass
class ScenarioContext:
    """Stable facades and scenario-local parameters passed into a user script."""

    app: AppFacade
    user: UserFacade
    llm: LLMView
    observability: BackendObservability
    params: ScenarioParameters = field(default_factory=ScenarioParameters)


@dataclass(frozen=True)
class ScenarioResult:
    id: str
    status: str
    error: str = ""
    error_type: str = ""
    traceback: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "error_type": self.error_type,
            "traceback": self.traceback,
        }


class ScenarioRegistry:
    """Register and select user scripts without embedding run orchestration."""

    def __init__(self) -> None:
        self._items: dict[str, ScenarioDefinition] = {}

    def register(self, definition: ScenarioDefinition) -> None:
        if definition.id in self._items:
            raise ValueError(f"duplicate scenario: {definition.id}")
        self._items[definition.id] = definition

    def select(
        self,
        *,
        suite: str,
        profile: str,
        scenario_ids: tuple[str, ...] = (),
    ) -> list[ScenarioDefinition]:
        requested = set(scenario_ids)
        unknown = requested.difference(self._items)
        if unknown:
            raise LookupError(f"unknown scenarios: {', '.join(sorted(unknown))}")
        selected = []
        for definition in self._items.values():
            if requested and definition.id not in requested:
                continue
            if suite not in definition.suites:
                continue
            if definition.profiles and profile not in definition.profiles:
                continue
            selected.append(definition)
        return selected


async def execute_scenario(
    definition: ScenarioDefinition, context: ScenarioContext
) -> ScenarioResult:
    """Execute one script and preserve a stable failure summary."""
    try:
        result = definition.script(context)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # scenario failure belongs in run artifacts
        return ScenarioResult(
            id=definition.id,
            status="failed",
            error=str(exc) or repr(exc),
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
    return ScenarioResult(id=definition.id, status="passed")


async def execute_post_checks(
    definition: ScenarioDefinition, context: ScenarioContext
) -> ScenarioResult:
    """Execute framework-level checks after evidence refresh for one script."""
    try:
        for check in definition.post_checks:
            result = check(context)
            if inspect.isawaitable(result):
                await result
    except Exception as exc:
        return ScenarioResult(
            id=definition.id,
            status="failed",
            error=str(exc) or repr(exc),
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
    return ScenarioResult(id=definition.id, status="passed")
