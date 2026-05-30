"""S0: Environment and LLM mode connectivity scenario.

Verifies backend accessibility, default stub LLM mode, and optional real LLM
connectivity when explicitly enabled.
"""

from __future__ import annotations

from typing import Any

from ..core.config import VerifyConfig
from ..core.context import ensure_scenario_context
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..core.scenario import ScenarioBuilder, ScenarioRunner
from ..flows.audit import verify_backend_runtime
from ..flows.connectivity import (
    check_backend_health,
    check_settings,
    fetch_runtime_info,
    ping_llm,
    verify_llm_mode_configuration,
    verify_trace_headers,
)

SCENARIO_ID = "S0_connectivity"


async def run_s0(
    run_manager: RunManager, config: VerifyConfig, metrics: MetricsAggregator
) -> None:
    """Execute S0 scenario."""
    builder = ScenarioBuilder(
        SCENARIO_ID, "Environment and LLM mode connectivity"
    )

    builder.add_step(
        "health_check", "Call backend health endpoint", _step_health, timeout_s=10.0
    )
    builder.add_step(
        "runtime_info", "Get runtime and LLM mode info", _step_runtime, timeout_s=10.0
    )
    builder.add_step(
        "settings_check",
        "Verify settings endpoint returns config",
        _step_settings,
        timeout_s=10.0,
    )
    builder.add_step(
        "verify_runtime",
        "Check verify runtime endpoint",
        _step_verify_runtime,
        timeout_s=10.0,
    )
    builder.add_step(
        "verify_trace",
        "Verify trace headers are returned",
        _step_trace_headers,
        timeout_s=10.0,
    )
    builder.add_step(
        "llm_mode_check",
        "Validate configured LLM mode and provider requirements",
        _step_llm_mode_check,
        timeout_s=5.0,
    )
    builder.add_step(
        "llm_ping",
        "Minimal LLM connectivity probe for current mode",
        _step_llm_ping,
        timeout_s=max(30.0, float(config.llm.timeout_s)),
    )

    runner = ScenarioRunner(run_manager, config)
    ctx = {
        "run_manager": run_manager,
        "config": config,
        "metrics": metrics,
    }
    result = await runner.run(builder, context=ctx)

    if result.status.value != "passed":
        raise RuntimeError(f"S0 failed: {result.failure_summary}")


async def _step_health(ctx: dict[str, Any]) -> None:
    await check_backend_health(ctx, scenario_id=SCENARIO_ID, step_id="health_check")


async def _step_runtime(ctx: dict[str, Any]) -> None:
    await fetch_runtime_info(ctx, scenario_id=SCENARIO_ID, step_id="runtime_info")


async def _step_settings(ctx: dict[str, Any]) -> None:
    await check_settings(ctx, scenario_id=SCENARIO_ID, step_id="settings_check")


async def _step_verify_runtime(ctx: dict[str, Any]) -> None:
    await verify_backend_runtime(
        ensure_scenario_context(ctx),
        scenario_id=SCENARIO_ID,
        step_id="verify_runtime",
    )


async def _step_trace_headers(ctx: dict[str, Any]) -> None:
    await verify_trace_headers(ctx, scenario_id=SCENARIO_ID, step_id="verify_trace")


async def _step_llm_mode_check(ctx: dict[str, Any]) -> None:
    await verify_llm_mode_configuration(
        ctx, scenario_id=SCENARIO_ID, step_id="llm_mode_check"
    )


async def _step_llm_ping(ctx: dict[str, Any]) -> None:
    await ping_llm(ctx, scenario_id=SCENARIO_ID, step_id="llm_ping")
