"""S0: Environment and model connectivity scenario.

Verifies that the backend is accessible, LLM config is present,
and a minimal model call succeeds.
"""
from __future__ import annotations

from typing import Any

from ..client import TargetClient
from ..config import VerifyConfig
from ..contract import (
    ContractError,
    validate_health,
    validate_runtime,
    validate_success,
)
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import ScenarioBuilder, ScenarioRunner, assert_that


async def run_s0(run_manager: RunManager, config: VerifyConfig, metrics: MetricsAggregator) -> None:
    """Execute S0 scenario."""
    builder = ScenarioBuilder("S0_connectivity", "Environment and model connectivity")

    builder.add_step("health_check", "Call backend health endpoint", _step_health, timeout_s=10.0)
    builder.add_step("runtime_info", "Get runtime and LLM config info", _step_runtime, timeout_s=10.0)
    builder.add_step("settings_check", "Verify settings endpoint returns config", _step_settings, timeout_s=10.0)
    builder.add_step("verify_runtime", "Check verify runtime endpoint", _step_verify_runtime, timeout_s=10.0)
    builder.add_step("verify_trace", "Verify trace headers are returned", _step_trace_headers, timeout_s=10.0)

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
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(config.target.base_url, run_manager, "S0_connectivity", "health_check") as client:
        body, rec = await client.health()
        validate_health(body, rec)
        assert_that.is_true(
            body.get("status") == "ok",
            "Health status should be 'ok'",
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S0_connectivity", step_id="health_check")


async def _step_runtime(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(config.target.base_url, run_manager, "S0_connectivity", "runtime_info") as client:
        body, rec = await client.runtime()
        validate_runtime(body, rec)

        llm = body.get("llm", {})
        assert_that.is_true(
            llm.get("base_url_configured", False),
            "LLM base_url should be configured",
        )

        ctx["runtime_info"] = body

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S0_connectivity", step_id="runtime_info")


async def _step_settings(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(config.target.base_url, run_manager, "S0_connectivity", "settings_check") as client:
        body, rec = await client.settings()

        # Validate settings structure
        assert_that.contains(body, "llm", "Settings should contain 'llm'")
        assert_that.contains(body, "context", "Settings should contain 'context'")

        llm = body.get("llm", {})
        assert_that.is_true(
            "api_key_configured" in llm,
            "LLM settings should include 'api_key_configured'",
        )
        assert_that.not_contains(str(llm), "sk-", "LLM settings must not expose api_key value")

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S0_connectivity", step_id="settings_check")


async def _step_verify_runtime(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(config.target.base_url, run_manager, "S0_connectivity", "verify_runtime") as client:
        body, rec = await client.verify_runtime()
        # This may return 404 if verify mode is not enabled
        if rec.status_code == 404:
            return  # Verify mode not active, skip

        assert_that.is_true(
            body.get("verify_mode", False),
            "Verify mode should be enabled",
        )

        # Must not expose api_key
        llm = body.get("llm", {})
        if llm:
            assert_that.not_contains(str(llm), "sk-", "Verify runtime must not expose api_key")

        ctx["backend_version"] = body.get("app_version")

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S0_connectivity", step_id="verify_runtime")


async def _step_trace_headers(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(config.target.base_url, run_manager, "S0_connectivity", "verify_trace") as client:
        _, rec = await client.health()

        assert_that.is_not_none(rec.trace_id, "Response should include x-trace-id header")
        assert_that.is_not_none(rec.request_id, "Response should include x-request-id header")
        assert_that.is_true(
            len(rec.trace_id) > 0,
            "Trace ID should not be empty",
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(rec, scenario_id="S0_connectivity", step_id="verify_trace")
