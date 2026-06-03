"""S0: verify-mode runtime and LLM connectivity."""

from __future__ import annotations

from vibe_verify.assertions import fail
from vibe_verify.scenario import ScenarioContext, ScenarioDefinition

from .common import CORE_SUITES, S0_SCENARIO_ID


def s0_environment_connectivity() -> ScenarioDefinition:
    return ScenarioDefinition(
        id=S0_SCENARIO_ID,
        script=run_s0_environment_connectivity,
        suites=CORE_SUITES,
        description="S0: verify mode runtime and LLM connectivity",
    )


async def run_s0_environment_connectivity(context: ScenarioContext) -> None:
    runtime = await context.observability.runtime()
    if runtime.get("verify_mode") is not True:
        fail("backend verify mode is not enabled", actual=runtime.get("verify_mode"))
    llm = runtime.get("llm")
    if not isinstance(llm, dict) or llm.get("base_url_configured") is not True:
        fail("backend LLM base URL is not configured", actual=llm)

    ping = await context.observability.llm_ping()
    if ping.get("ok") is not True:
        fail("LLM ping failed", actual=ping)
    if not str(ping.get("model", "")).strip():
        fail("LLM ping model missing")
    tokens = ping.get("tokens")
    if not isinstance(tokens, dict):
        fail("LLM ping tokens missing", actual=tokens)
    for key in ("input", "output", "total"):
        value = tokens.get(key)
        if value is not None and int(value) < 0:
            fail("LLM ping token count must be non-negative", key=key, actual=value)
