from __future__ import annotations

import pytest

from tests.system_verify.scenarios.registry import (
    MVP_SCENARIO_IDS,
    SCENARIOS,
    normalize_suite_name,
    scenarios_requiring_probe,
    select_scenarios_for_run,
)


def test_mvp_registry_contains_s0_through_s6() -> None:
    selected = select_scenarios_for_run(suite="mvp", profile_name="mvp_stub")
    assert [scenario.id for scenario in selected] == list(MVP_SCENARIO_IDS)
    assert [scenario.id for scenario in selected][-2:] == [
        "S5_direct_chat",
        "S6_followup_chat",
    ]


def test_smoke_suite_alias_matches_mvp_scenarios() -> None:
    smoke = select_scenarios_for_run(suite="smoke", profile_name="mvp_stub")
    mvp = select_scenarios_for_run(suite="mvp", profile_name="mvp_stub")
    assert [scenario.id for scenario in smoke] == [scenario.id for scenario in mvp]


def test_normalize_suite_name_maps_smoke_to_mvp() -> None:
    assert normalize_suite_name("smoke") == "mvp"
    assert normalize_suite_name("real-happy-path") == "real-happy-path"


def test_real_happy_path_a2_selects_comments_scenario() -> None:
    selected = select_scenarios_for_run(
        suite="real-happy-path",
        profile_name="r1_a2_stub",
        coverage="A2",
    )
    assert [scenario.id for scenario in selected] == ["R1_A2_comments"]


def test_real_happy_path_a3_selects_compaction_scenario() -> None:
    selected = select_scenarios_for_run(
        suite="real-happy-path",
        profile_name="r1_a3_real",
        coverage="A3",
    )
    assert [scenario.id for scenario in selected] == ["R1_A3_compaction"]


def test_real_happy_path_a4_selects_full_flow_scenario() -> None:
    selected = select_scenarios_for_run(
        suite="real-happy-path",
        profile_name="r1_a4_stub",
        coverage="A4",
    )
    assert [scenario.id for scenario in selected] == ["R1_A4_full_flow"]


def test_profile_filter_excludes_mvp_from_r1_scenarios() -> None:
    selected = select_scenarios_for_run(
        suite="real-happy-path",
        profile_name="mvp_stub",
        coverage="A2",
    )
    assert selected == []


def test_allowed_profiles_include_stub_and_real_variants() -> None:
    assert SCENARIOS["R1_A2_comments"].allowed_profiles == frozenset(
        {"r1_a2_stub", "r1_a2_real"}
    )
    assert SCENARIOS["R1_A3_compaction"].allowed_profiles == frozenset(
        {"r1_a3_stub", "r1_a3_real"}
    )


def test_required_probe_filtering() -> None:
    happy_path = scenarios_requiring_probe("happy_path_current")
    assert {scenario.id for scenario in happy_path} == {
        "R1_A2_comments",
        "R1_A3_compaction",
        "R1_A4_full_flow",
    }

    early = scenarios_requiring_probe("early")
    assert {scenario.id for scenario in early} == {
        "S2_continuous_reading",
        "S5_direct_chat",
        "S6_followup_chat",
    }


def test_select_by_scenario_id_ignores_suite_tags() -> None:
    selected = select_scenarios_for_run(
        suite="mvp",
        profile_name="mvp_stub",
        scenario_id="S0_connectivity",
    )
    assert len(selected) == 1
    assert selected[0].id == "S0_connectivity"


def test_unknown_scenario_id_raises() -> None:
    with pytest.raises(KeyError, match="Unknown scenario"):
        select_scenarios_for_run(
            suite="mvp",
            profile_name="mvp_stub",
            scenario_id="missing",
        )


def test_scenario_profile_mismatch_raises() -> None:
    with pytest.raises(RuntimeError, match="does not allow profile"):
        select_scenarios_for_run(
            suite="real-happy-path",
            profile_name="r1_a3_stub",
            scenario_id="R1_A2_comments",
        )
