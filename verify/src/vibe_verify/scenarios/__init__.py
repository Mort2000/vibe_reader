"""Built-in user-script registry for Vibe Reader verification."""

from __future__ import annotations

from vibe_verify.scenario import ScenarioRegistry

from .r1_full_flow import R1_A4_SCENARIO_ID, r1_a4_full_flow


def build_registry() -> ScenarioRegistry:
    registry = ScenarioRegistry()
    registry.register(r1_a4_full_flow())
    return registry


__all__ = ["R1_A4_SCENARIO_ID", "build_registry", "r1_a4_full_flow"]
