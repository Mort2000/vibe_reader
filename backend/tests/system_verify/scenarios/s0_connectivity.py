"""S0: Environment and model connectivity scenario.

Verifies that the backend is accessible, LLM config is present,
and a minimal model call succeeds.
"""

from __future__ import annotations

from typing import Any

from ..client import TargetClient
from ..config import VerifyConfig
from ..contract import (
    validate_health,
    validate_runtime,
)
from ..data_lifecycle import assert_isolated_data_dir
from ..metrics_collector import MetricsAggregator
from ..run import RunManager
from ..scenario import ScenarioBuilder, ScenarioRunner, StepAssertionError, assert_that


async def run_s0(
    run_manager: RunManager, config: VerifyConfig, metrics: MetricsAggregator
) -> None:
    """Execute S0 scenario."""
    builder = ScenarioBuilder("S0_connectivity", "Environment and model connectivity")

    builder.add_step(
        "health_check", "Call backend health endpoint", _step_health, timeout_s=10.0
    )
    builder.add_step(
        "runtime_info", "Get runtime and LLM config info", _step_runtime, timeout_s=10.0
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
        "llm_ping",
        "Minimal LLM connectivity probe",
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
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S0_connectivity",
        "health_check",
        context=ctx,
    ) as client:
        body, rec = await client.health()
        validate_health(body, rec)
        assert_that.is_true(
            body.get("status") == "ok",
            "Health status should be 'ok'",
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S0_connectivity", step_id="health_check"
        )


async def _step_runtime(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S0_connectivity",
        "runtime_info",
        context=ctx,
    ) as client:
        body, rec = await client.runtime()
        validate_runtime(body, rec)

        assert_isolated_data_dir(config.target_data_dir, body.get("data_dir", ""))

        llm = body.get("llm", {})
        assert_that.is_true(
            llm.get("base_url_configured", False),
            "LLM base_url should be configured",
        )

        ctx["runtime_info"] = body

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S0_connectivity", step_id="runtime_info"
        )


async def _step_settings(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S0_connectivity",
        "settings_check",
        context=ctx,
    ) as client:
        body, rec = await client.settings()

        assert_that.contains(body, "llm", "Settings should contain 'llm'")
        assert_that.contains(body, "context", "Settings should contain 'context'")

        llm = body.get("llm", {})
        assert_that.is_true(
            "api_key_configured" in llm,
            "LLM settings should include 'api_key_configured'",
        )
        assert_that.not_contains(
            str(llm), "sk-", "LLM settings must not expose api_key value"
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S0_connectivity", step_id="settings_check"
        )


async def _step_verify_runtime(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S0_connectivity",
        "verify_runtime",
        context=ctx,
    ) as client:
        body, rec = await client.verify_runtime()
        if rec.status_code == 404:
            ctx["verify_mode_active"] = False
            return

        assert_that.is_true(
            body.get("verify_mode", False),
            "Verify mode should be enabled",
        )

        llm = body.get("llm", {})
        if llm:
            assert_that.not_contains(
                str(llm), "sk-", "Verify runtime must not expose api_key"
            )

        ctx["verify_mode_active"] = True
        ctx["backend_version"] = body.get("app_version")
        run_manager.set_backend_version(body.get("app_version"))

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S0_connectivity", step_id="verify_runtime"
        )


async def _step_trace_headers(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S0_connectivity",
        "verify_trace",
        context=ctx,
    ) as client:
        _, rec = await client.health()

        assert_that.is_not_none(
            rec.trace_id, "Response should include x-trace-id header"
        )
        assert_that.is_not_none(
            rec.request_id, "Response should include x-request-id header"
        )
        assert_that.is_true(
            len(rec.trace_id) > 0,
            "Trace ID should not be empty",
        )

        metrics: MetricsAggregator = ctx["metrics"]
        metrics.record_from_api_record(
            rec, scenario_id="S0_connectivity", step_id="verify_trace"
        )


async def _step_llm_ping(ctx: dict[str, Any]) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]

    if not ctx.get("verify_mode_active", False):
        runtime = ctx.get("runtime_info", {})
        llm = runtime.get("llm", {})
        if not llm.get("api_key_configured", False):
            raise StepAssertionError(
                assertion="llm_not_configured",
                message="LLM api_key is not configured; cannot run connectivity probe",
            )
        metrics.record(
            "llm.ping.skipped",
            1,
            unit="count",
            scenario_id="S0_connectivity",
            step_id="llm_ping",
            tags={"reason": "verify_mode_inactive"},
        )
        return

    async with TargetClient(
        config.target.base_url,
        run_manager,
        "S0_connectivity",
        "llm_ping",
        timeout=float(config.llm.timeout_s),
        context=ctx,
    ) as client:
        body, rec = await client.verify_llm_ping()

        if rec.status_code >= 400:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
            raise StepAssertionError(
                assertion="llm_ping_failed",
                message=f"LLM ping failed with status {rec.status_code}: {code}",
                actual=body,
            )

        assert_that.is_true(body.get("ok", False), "LLM ping should return ok=true")
        ping_trace_id = body.get("trace_id") or rec.trace_id
        assert_that.is_not_none(ping_trace_id, "LLM ping should include trace_id")

        tokens = body.get("tokens") or {}
        has_usage = body.get("usage_estimate", False) or any(
            tokens.get(k) is not None for k in ("input", "output", "cached_input")
        )
        assert_that.is_true(has_usage, "LLM ping should record usage or usage estimate")

        if rec.trace_id or ping_trace_id:
            metrics.record_trace(
                trace_id=rec.trace_id or ping_trace_id,
                request_id=rec.request_id,
                scenario_id="S0_connectivity",
                step_id="llm_ping",
                agent="llm_ping",
            )
        metrics.record_from_api_record(
            rec, scenario_id="S0_connectivity", step_id="llm_ping"
        )

        if body.get("latency_ms") is not None:
            metrics.record(
                "llm.ping.latency_ms",
                body["latency_ms"],
                unit="ms",
                scenario_id="S0_connectivity",
                step_id="llm_ping",
            )
        for key in ("input", "output", "cached_input"):
            if tokens.get(key) is not None:
                metrics.record(
                    f"llm.ping.tokens.{key}",
                    tokens[key],
                    unit="count",
                    scenario_id="S0_connectivity",
                    step_id="llm_ping",
                )
