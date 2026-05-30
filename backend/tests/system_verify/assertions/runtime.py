"""Runtime mode assertions (verify mode, LLM mode, model match).

Pure validation helpers — no HTTP calls, reading advance, or audit file I/O.
"""

from __future__ import annotations

from typing import Any

from ..core.config import VerifyConfig, validate_real_llm_config
from ..core.scenario import StepAssertionError, assert_that
from ..profiles.policies import AssertionPolicy
from ..profiles.registry import VerificationProfile, profile_from_param_set


def profile_from_config(config: VerifyConfig) -> VerificationProfile:
    return profile_from_param_set(config.params)


def assert_health_ok(body: dict[str, Any]) -> None:
    assert_that.is_true(body.get("status") == "ok", "Health status should be 'ok'")


def assert_runtime_llm_for_profile(
    llm: dict[str, Any],
    *,
    llm_mode: str,
) -> None:
    """Assert backend runtime LLM fields match the active profile mode."""
    if llm_mode == "real":
        assert_that.is_true(
            llm.get("base_url_configured", False),
            "Real LLM mode requires configured base_url",
        )


def assert_settings_response(body: dict[str, Any]) -> None:
    assert_that.contains(body, "llm", "Settings should contain 'llm'")
    assert_that.contains(body, "context", "Settings should contain 'context'")

    llm = body.get("llm", {})
    assert_that.is_true(
        "api_key_configured" in llm,
        "LLM settings should include 'api_key_configured'",
    )
    assert_that.not_contains(str(llm), "sk-", "LLM settings must not expose api_key value")


def assert_trace_headers(trace_id: str | None, request_id: str | None) -> None:
    assert_that.is_not_none(trace_id, "Response should include x-trace-id header")
    assert_that.is_not_none(request_id, "Response should include x-request-id header")
    assert_that.is_true(len(trace_id or "") > 0, "Trace ID should not be empty")


def assert_llm_mode_configuration(
    config: VerifyConfig,
    *,
    policy: AssertionPolicy | None = None,
) -> None:
    """Validate verify-side LLM mode configuration for the active profile."""
    _ = policy  # reserved for future strict/relaxed policy hooks
    profile = profile_from_config(config)
    assert_that.is_true(
        profile.llm_mode in ("stub", "real"),
        "llm.mode must be 'stub' or 'real'",
    )
    if profile.llm_mode == "real":
        errors = validate_real_llm_config(config)
        if errors:
            raise StepAssertionError(
                assertion="real_llm_config",
                message="; ".join(errors),
                actual=errors,
            )
        return

    errors = validate_real_llm_config(config)
    assert_that.is_true(
        len(errors) == 0 or not config.real_llm.api_key,
        "Stub mode must not require real LLM credentials",
    )


def assert_llm_ping_prerequisites(
    llm_runtime: dict[str, Any],
    *,
    llm_mode: str,
) -> None:
    api_key_configured = llm_runtime.get("api_key_configured", False)
    if llm_mode == "real" and not api_key_configured:
        raise StepAssertionError(
            assertion="real_llm_api_key_missing",
            message="Real LLM mode requires configured API key",
        )
    if llm_mode == "stub" and not api_key_configured:
        raise StepAssertionError(
            assertion="stub_llm_not_configured",
            message=(
                "Stub mode requires backend LLM env pointing at AIMock sidecar. "
                "Set VIBE_READER_LLM_BASE_URL, VIBE_READER_LLM_API_KEY, "
                "VIBE_READER_LLM_MODEL and restart backend."
            ),
            actual=llm_runtime,
        )


def assert_llm_ping_response(body: dict[str, Any], rec: Any) -> str | None:
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
    return ping_trace_id


def assert_reading_not_blocked_timing(
    elapsed_ms: float,
    *,
    max_duration_ms: float = 5000.0,
) -> None:
    """Assert a progress update completed within the latency budget."""
    if elapsed_ms > max_duration_ms:
        raise StepAssertionError(
            assertion="reading_not_blocked",
            message=f"Progress update took {elapsed_ms:.0f}ms (> {max_duration_ms:.0f}ms)",
            actual=elapsed_ms,
            expected=max_duration_ms,
        )
