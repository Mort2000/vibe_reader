"""Built-in user-script registry for Vibe Reader verification."""

from __future__ import annotations

from vibe_verify.scenario import ScenarioRegistry

from .r1_full_flow import R1_A4_SCENARIO_ID, r1_a4_full_flow
from .s0_s6 import (
    S0_SCENARIO_ID,
    S1_SCENARIO_ID,
    S2_SCENARIO_ID,
    S3_SCENARIO_ID,
    S4_SCENARIO_ID,
    S5_SCENARIO_ID,
    S6_SCENARIO_ID,
    s0_to_s6_scenarios,
)


def build_registry() -> ScenarioRegistry:
    registry = ScenarioRegistry()
    for scenario in s0_to_s6_scenarios():
        registry.register(scenario)
    registry.register(r1_a4_full_flow())
    return registry


__all__ = [
    "R1_A4_SCENARIO_ID",
    "S0_SCENARIO_ID",
    "S1_SCENARIO_ID",
    "S2_SCENARIO_ID",
    "S3_SCENARIO_ID",
    "S4_SCENARIO_ID",
    "S5_SCENARIO_ID",
    "S6_SCENARIO_ID",
    "build_registry",
    "r1_a4_full_flow",
    "s0_to_s6_scenarios",
]
