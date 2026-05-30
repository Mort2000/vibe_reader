"""Backend connectivity checks: health, runtime, settings, trace, LLM ping."""

from __future__ import annotations

from typing import Any

from ..assertions import runtime as runtime_assertions
from ..core.client_factory import TargetClient
from ..core.config import VerifyConfig
from ..assertions.api_contracts import validate_health, validate_runtime
from ..data_lifecycle import assert_isolated_data_dir
from ..metrics_collector import MetricsAggregator
from ..core.run_manager import RunManager
from ..profiles.registry import profile_from_param_set


async def check_backend_health(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "health_check",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.health()
        validate_health(body, rec)
        runtime_assertions.assert_health_ok(body)
        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def fetch_runtime_info(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "runtime_info",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    profile = profile_from_param_set(config.params)

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.runtime()
        validate_runtime(body, rec)

        assert_isolated_data_dir(config.target_data_dir, body.get("data_dir", ""))
        runtime_assertions.assert_runtime_llm_for_profile(
            body.get("llm", {}),
            llm_mode=profile.llm_mode,
        )

        ctx["runtime_info"] = body
        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def check_settings(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "settings_check",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        body, rec = await client.settings()
        runtime_assertions.assert_settings_response(body)
        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def verify_trace_headers(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "verify_trace",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        context=ctx,
    ) as client:
        _, rec = await client.health()
        runtime_assertions.assert_trace_headers(rec.trace_id, rec.request_id)
        metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)


async def verify_llm_mode_configuration(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "llm_mode_check",
) -> None:
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    profile = profile_from_param_set(config.params)

    runtime_assertions.assert_llm_mode_configuration(
        config,
        policy=profile.assertion_policy,
    )
    metrics.record(
        "llm.mode",
        1,
        unit="count",
        scenario_id=scenario_id,
        step_id=step_id,
        tags={"mode": profile.llm_mode},
    )


async def ping_llm(
    ctx: dict[str, Any],
    *,
    scenario_id: str,
    step_id: str = "llm_ping",
) -> None:
    run_manager: RunManager = ctx["run_manager"]
    config: VerifyConfig = ctx["config"]
    metrics: MetricsAggregator = ctx["metrics"]
    profile = profile_from_param_set(config.params)

    if not ctx.get("verify_mode_active", False):
        metrics.record(
            "llm.ping.skipped",
            1,
            unit="count",
            scenario_id=scenario_id,
            step_id=step_id,
            tags={"reason": "verify_mode_inactive"},
        )
        return

    runtime = ctx.get("runtime_info", {})
    llm_runtime = runtime.get("llm", {})
    runtime_assertions.assert_llm_ping_prerequisites(
        llm_runtime,
        llm_mode=profile.llm_mode,
    )

    async with TargetClient(
        config.target.base_url,
        run_manager,
        scenario_id,
        step_id,
        timeout=float(config.llm.timeout_s),
        context=ctx,
    ) as client:
        body, rec = await client.verify_llm_ping()
        ping_trace_id = runtime_assertions.assert_llm_ping_response(body, rec)
        _record_ping_success(metrics, body, rec, ping_trace_id, scenario_id, step_id)


def _record_ping_success(
    metrics: MetricsAggregator,
    body: dict[str, Any],
    rec: Any,
    ping_trace_id: str | None,
    scenario_id: str,
    step_id: str,
) -> None:
    tokens = body.get("tokens") or {}
    if rec.trace_id or ping_trace_id:
        metrics.record_trace(
            trace_id=rec.trace_id or ping_trace_id or "",
            request_id=rec.request_id,
            scenario_id=scenario_id,
            step_id=step_id,
            agent="llm_ping",
        )
    metrics.record_from_api_record(rec, scenario_id=scenario_id, step_id=step_id)

    if body.get("latency_ms") is not None:
        metrics.record(
            "llm.ping.latency_ms",
            body["latency_ms"],
            unit="ms",
            scenario_id=scenario_id,
            step_id=step_id,
        )
    for key in ("input", "output", "cached_input"):
        if tokens.get(key) is not None:
            metrics.record(
                f"llm.ping.tokens.{key}",
                tokens[key],
                unit="count",
                scenario_id=scenario_id,
                step_id=step_id,
            )
