"""Scenario runner: composable, replayable scenario execution with result recording."""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

from .run import RunManager
from .config import VerifyConfig


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class ScenarioStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass
class StepAssertionError(Exception):
    assertion: str
    message: str
    expected: Any = None
    actual: Any = None


@dataclass
class StepResult:
    step_id: str
    description: str
    status: StepStatus = StepStatus.PENDING
    duration_ms: float | None = None
    assertions_passed: int = 0
    assertions_total: int = 0
    errors: list[StepAssertionError] = field(default_factory=list)
    trace_id: str = ""
    request_id: str = ""
    failure_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "assertions_passed": self.assertions_passed,
            "assertions_total": self.assertions_total,
            "errors": [
                {
                    "assertion": e.assertion,
                    "message": e.message,
                    "expected": e.expected,
                    "actual": e.actual,
                }
                for e in self.errors
            ],
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "failure_context": self.failure_context,
        }


@dataclass
class ScenarioResult:
    scenario_id: str
    description: str
    status: ScenarioStatus = ScenarioStatus.PENDING
    started_at: str = ""
    ended_at: str = ""
    steps: list[StepResult] = field(default_factory=list)
    failure_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "description": self.description,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "steps": [s.to_dict() for s in self.steps],
            "failure_summary": self.failure_summary,
        }


StepFunc = Callable[..., Awaitable[None]]


def _step_failure_detail(step_result: StepResult) -> str:
    if step_result.errors:
        return step_result.errors[0].message
    return step_result.description


@dataclass
class Step:
    step_id: str
    description: str
    func: StepFunc
    timeout_s: float = 60.0
    retry_count: int = 0
    retry_delay_s: float = 1.0


class ScenarioBuilder:
    """Builds a scenario with ordered steps."""

    def __init__(self, scenario_id: str, description: str):
        self.scenario_id = scenario_id
        self.description = description
        self._steps: list[Step] = []
        self.continue_on_failure = False

    def step(
        self,
        step_id: str,
        description: str,
        timeout_s: float = 60.0,
        retry_count: int = 0,
        retry_delay_s: float = 1.0,
    ) -> Callable:
        """Decorator to add a step to this scenario."""

        def decorator(func: StepFunc) -> StepFunc:
            self._steps.append(
                Step(
                    step_id=step_id,
                    description=description,
                    func=func,
                    timeout_s=timeout_s,
                    retry_count=retry_count,
                    retry_delay_s=retry_delay_s,
                )
            )
            return func

        return decorator

    def add_step(
        self,
        step_id: str,
        description: str,
        func: StepFunc,
        timeout_s: float = 60.0,
        retry_count: int = 0,
        retry_delay_s: float = 1.0,
    ) -> None:
        self._steps.append(
            Step(
                step_id=step_id,
                description=description,
                func=func,
                timeout_s=timeout_s,
                retry_count=retry_count,
                retry_delay_s=retry_delay_s,
            )
        )

    @property
    def steps(self) -> list[Step]:
        return list(self._steps)


class ScenarioRunner:
    """Executes scenarios and records results."""

    def __init__(self, run_manager: RunManager, config: VerifyConfig):
        self.run_manager = run_manager
        self.config = config
        self._results: list[ScenarioResult] = []

    async def run(
        self, builder: ScenarioBuilder, context: dict[str, Any] | None = None
    ) -> ScenarioResult:
        """Execute all steps in a scenario."""
        ctx = context or {}
        result = ScenarioResult(
            scenario_id=builder.scenario_id,
            description=builder.description,
            status=ScenarioStatus.RUNNING,
            started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        failed_steps: list[str] = []

        for step_def in builder.steps:
            step_result = await self._run_step(step_def, ctx)
            result.steps.append(step_result)

            if step_result.status in (StepStatus.FAILED, StepStatus.ERROR):
                result.status = ScenarioStatus.FAILED
                failed_steps.append(step_result.step_id)
                if not builder.continue_on_failure:
                    detail = _step_failure_detail(step_result)
                    result.failure_summary = (
                        f"Step '{step_result.step_id}' failed: {detail}"
                    )
                    break

        if failed_steps and builder.continue_on_failure:
            result.failure_summary = f"Failed steps: {', '.join(failed_steps)}"
        elif result.status == ScenarioStatus.FAILED and not result.failure_summary:
            last = failed_steps[-1] if failed_steps else ""
            failed_step = next((s for s in result.steps if s.step_id == last), None)
            detail = (
                _step_failure_detail(failed_step) if failed_step is not None else last
            )
            result.failure_summary = f"Step '{last}' failed: {detail}"

        if result.status == ScenarioStatus.RUNNING:
            result.status = ScenarioStatus.PASSED

        result.ended_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        self._results.append(result)
        self.run_manager.write_ndjson("scenario_results.ndjson", [result.to_dict()])
        return result

    async def _run_step(self, step_def: Step, context: dict[str, Any]) -> StepResult:
        step_result = StepResult(
            step_id=step_def.step_id,
            description=step_def.description,
            status=StepStatus.RUNNING,
        )

        import time

        start = time.monotonic()

        attempts = 1 + step_def.retry_count
        for attempt in range(attempts):
            try:
                await asyncio.wait_for(
                    step_def.func(context),
                    timeout=step_def.timeout_s,
                )
                step_result.status = StepStatus.PASSED
                break
            except StepAssertionError as exc:
                step_result.errors.append(exc)
                step_result.status = StepStatus.FAILED
                step_result.failure_context["traceback"] = traceback.format_exc()
                if attempt < attempts - 1:
                    await asyncio.sleep(step_def.retry_delay_s)
                    continue
                break
            except asyncio.TimeoutError:
                step_result.errors.append(
                    StepAssertionError(
                        assertion="timeout",
                        message=f"Step timed out after {step_def.timeout_s}s",
                    )
                )
                step_result.status = StepStatus.FAILED
                break
            except Exception as exc:
                step_result.errors.append(
                    StepAssertionError(
                        assertion="unexpected_error",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
                step_result.status = StepStatus.ERROR
                step_result.failure_context["traceback"] = traceback.format_exc()
                break

        elapsed = (time.monotonic() - start) * 1000
        step_result.duration_ms = elapsed
        step_result.assertions_total = len(step_result.errors) + (
            1 if step_result.status == StepStatus.PASSED else 0
        )
        step_result.assertions_passed = (
            1 if step_result.status == StepStatus.PASSED else 0
        )

        last_rec = context.get("last_api_record")
        if last_rec is not None:
            step_result.trace_id = getattr(last_rec, "trace_id", "") or ""
            step_result.request_id = getattr(last_rec, "request_id", "") or ""
            if step_result.status in (StepStatus.FAILED, StepStatus.ERROR):
                step_result.failure_context.setdefault(
                    "last_api_record",
                    last_rec.to_dict()
                    if hasattr(last_rec, "to_dict")
                    else str(last_rec),
                )

        return step_result

    @property
    def results(self) -> list[ScenarioResult]:
        return list(self._results)

    def passed_count(self) -> int:
        return sum(1 for r in self._results if r.status == ScenarioStatus.PASSED)

    def failed_count(self) -> int:
        return sum(
            1
            for r in self._results
            if r.status in (ScenarioStatus.FAILED, ScenarioStatus.ERROR)
        )


class ScenarioAssertion:
    """Helper for writing assertions inside scenario steps."""

    @staticmethod
    def check(
        condition: bool, message: str, expected: Any = None, actual: Any = None
    ) -> None:
        if not condition:
            raise StepAssertionError(
                assertion="assertion_failed",
                message=message,
                expected=expected,
                actual=actual,
            )

    @staticmethod
    def equal(actual: Any, expected: Any, label: str = "") -> None:
        if actual != expected:
            msg = f"Expected {expected}, got {actual}"
            if label:
                msg = f"{label}: {msg}"
            raise StepAssertionError(
                assertion="assert_equal", message=msg, expected=expected, actual=actual
            )

    @staticmethod
    def gte(actual: float | int, minimum: float | int, label: str = "") -> None:
        if actual < minimum:
            msg = f"Expected >= {minimum}, got {actual}"
            if label:
                msg = f"{label}: {msg}"
            raise StepAssertionError(
                assertion="assert_gte", message=msg, expected=minimum, actual=actual
            )

    @staticmethod
    def lte(actual: float | int, maximum: float | int, label: str = "") -> None:
        if actual > maximum:
            msg = f"Expected <= {maximum}, got {actual}"
            if label:
                msg = f"{label}: {msg}"
            raise StepAssertionError(
                assertion="assert_lte", message=msg, expected=maximum, actual=actual
            )

    @staticmethod
    def contains(container: Any, item: Any, label: str = "") -> None:
        if item not in container:
            msg = f"Expected to contain {item}"
            if label:
                msg = f"{label}: {msg}"
            raise StepAssertionError(
                assertion="assert_contains", message=msg, actual=container
            )

    @staticmethod
    def not_contains(container: Any, item: Any, label: str = "") -> None:
        if item in container:
            msg = f"Expected NOT to contain {item}"
            if label:
                msg = f"{label}: {msg}"
            raise StepAssertionError(
                assertion="assert_not_contains", message=msg, actual=container
            )

    @staticmethod
    def is_not_none(value: Any, label: str = "") -> None:
        if value is None:
            msg = "Expected non-None value"
            if label:
                msg = f"{label}: {msg}"
            raise StepAssertionError(assertion="assert_not_none", message=msg)

    @staticmethod
    def is_true(value: bool, message: str = "Expected True") -> None:
        if not value:
            raise StepAssertionError(assertion="assert_true", message=message)


assert_that = ScenarioAssertion()
