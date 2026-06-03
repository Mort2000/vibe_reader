"""Built-in user-script registry for Vibe Reader verification."""

from __future__ import annotations

from vibe_verify.scenario import ScenarioRegistry

from .common import (
    S0_SCENARIO_ID,
    S1_SCENARIO_ID,
    S2_SCENARIO_ID,
    S3_SCENARIO_ID,
    S4_SCENARIO_ID,
    S5_SCENARIO_ID,
    S6_SCENARIO_ID,
)
from .r1_full_flow import R1_A4_SCENARIO_ID, r1_a4_full_flow
from .s0_environment_connectivity import s0_environment_connectivity
from .s1_import_book import s1_import_book
from .s2_continuous_reading_comments import s2_continuous_reading_comments
from .s3_fast_scroll import s3_fast_scroll
from .s4_context_compaction import s4_context_compaction
from .s5_direct_chat import s5_direct_chat
from .s6_followup_chat import s6_followup_chat


def s0_to_s6_scenarios():
    return (
        s0_environment_connectivity(),
        s1_import_book(),
        s2_continuous_reading_comments(),
        s3_fast_scroll(),
        s4_context_compaction(),
        s5_direct_chat(),
        s6_followup_chat(),
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
