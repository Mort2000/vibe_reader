"""Scenario registry: metadata and suite selection for system verification."""

from __future__ import annotations

from typing import Any

from tests.system_verify.core.run_spec import ScenarioDefinition

SuiteName = str


async def _invoke_s0(handle: Any) -> None:
    from .s0_connectivity import run_s0

    await run_s0(handle.run_manager, handle.config, handle.metrics)


async def _invoke_s1(handle: Any) -> None:
    from .s1_import import run_s1

    await run_s1(
        handle.run_manager,
        handle.config,
        handle.metrics,
        handle.corpus,
        suite_ctx=handle.suite_ctx,
    )


async def _invoke_s2(handle: Any) -> None:
    from .s2_continuous_reading import run_s2

    await run_s2(
        handle.run_manager,
        handle.config,
        handle.metrics,
        handle.corpus,
        suite_ctx=handle.suite_ctx,
    )


async def _invoke_s3(handle: Any) -> None:
    from .s3_fast_scroll import run_s3

    await run_s3(
        handle.run_manager,
        handle.config,
        handle.metrics,
        handle.corpus,
        suite_ctx=handle.suite_ctx,
    )


async def _invoke_s4(handle: Any) -> None:
    from .s4_long_context import run_s4

    await run_s4(
        handle.run_manager,
        handle.config,
        handle.metrics,
        handle.corpus,
        suite_ctx=handle.suite_ctx,
    )


async def _invoke_s5(handle: Any) -> None:
    from .s5_direct_chat import run_s5

    await run_s5(
        handle.run_manager,
        handle.config,
        handle.metrics,
        handle.corpus,
        suite_ctx=handle.suite_ctx,
    )


async def _invoke_s6(handle: Any) -> None:
    from .s6_followup_chat import run_s6

    await run_s6(
        handle.run_manager,
        handle.config,
        handle.metrics,
        handle.corpus,
        suite_ctx=handle.suite_ctx,
    )


async def _invoke_r1_a2(handle: Any) -> None:
    from .r1_a2_comments import run

    await run(
        handle.run_manager,
        handle.config,
        handle.metrics,
        handle.corpus,
        suite_ctx=handle.suite_ctx,
    )


async def _invoke_r1_a3(handle: Any) -> None:
    from .r1_a3_compaction import run

    await run(
        handle.run_manager,
        handle.config,
        handle.metrics,
        handle.corpus,
        suite_ctx=handle.suite_ctx,
    )


async def _invoke_r1_a4(handle: Any) -> None:
    from .r1_a4_full_flow import run

    await run(
        handle.run_manager,
        handle.config,
        handle.metrics,
        handle.corpus,
        suite_ctx=handle.suite_ctx,
    )


SCENARIOS: dict[str, ScenarioDefinition] = {
    "S0_connectivity": ScenarioDefinition(
        id="S0_connectivity",
        suite_tags=frozenset({"smoke", "mvp"}),
        allowed_profiles=frozenset({"mvp_stub"}),
        required_probes=(),
        order=0,
        requires_corpus=False,
        invoke=_invoke_s0,
    ),
    "S1_book_import": ScenarioDefinition(
        id="S1_book_import",
        suite_tags=frozenset({"smoke", "mvp"}),
        allowed_profiles=frozenset({"mvp_stub"}),
        required_probes=(),
        order=10,
        invoke=_invoke_s1,
    ),
    "S2_continuous_reading": ScenarioDefinition(
        id="S2_continuous_reading",
        suite_tags=frozenset({"smoke", "mvp"}),
        allowed_profiles=frozenset({"mvp_stub"}),
        required_probes=("early",),
        order=20,
        invoke=_invoke_s2,
    ),
    "S3_fast_scroll": ScenarioDefinition(
        id="S3_fast_scroll",
        suite_tags=frozenset({"smoke", "mvp"}),
        allowed_profiles=frozenset({"mvp_stub"}),
        required_probes=(),
        order=30,
        invoke=_invoke_s3,
    ),
    "S4_long_context": ScenarioDefinition(
        id="S4_long_context",
        suite_tags=frozenset({"smoke", "mvp"}),
        allowed_profiles=frozenset({"mvp_stub"}),
        required_probes=(),
        order=40,
        invoke=_invoke_s4,
    ),
    "S5_direct_chat": ScenarioDefinition(
        id="S5_direct_chat",
        suite_tags=frozenset({"smoke", "mvp"}),
        allowed_profiles=frozenset({"mvp_stub"}),
        required_probes=("early",),
        order=50,
        invoke=_invoke_s5,
    ),
    "S6_followup_chat": ScenarioDefinition(
        id="S6_followup_chat",
        suite_tags=frozenset({"smoke", "mvp"}),
        allowed_profiles=frozenset({"mvp_stub"}),
        required_probes=("early",),
        order=60,
        invoke=_invoke_s6,
    ),
    "R1_A2_comments": ScenarioDefinition(
        id="R1_A2_comments",
        suite_tags=frozenset({"real-happy-path"}),
        allowed_profiles=frozenset({"r1_a2_stub", "r1_a2_real"}),
        required_probes=("happy_path_current",),
        order=0,
        coverage="A2",
        invoke=_invoke_r1_a2,
    ),
    "R1_A3_compaction": ScenarioDefinition(
        id="R1_A3_compaction",
        suite_tags=frozenset({"real-happy-path"}),
        allowed_profiles=frozenset({"r1_a3_stub", "r1_a3_real"}),
        required_probes=("happy_path_current",),
        order=0,
        coverage="A3",
        invoke=_invoke_r1_a3,
    ),
    "R1_A4_full_flow": ScenarioDefinition(
        id="R1_A4_full_flow",
        suite_tags=frozenset({"real-happy-path"}),
        allowed_profiles=frozenset({"r1_a4_stub", "r1_a4_real"}),
        required_probes=("happy_path_current",),
        order=0,
        coverage="A4",
        invoke=_invoke_r1_a4,
    ),
}

MVP_SCENARIO_IDS: tuple[str, ...] = tuple(
    scenario_id
    for scenario_id, definition in sorted(
        SCENARIOS.items(), key=lambda item: item[1].order
    )
    if "mvp" in definition.suite_tags
)


def normalize_suite_name(suite: str) -> str:
    """Map CLI suite aliases to registry lookup keys."""
    return "mvp" if suite == "smoke" else suite


def select_scenarios_for_run(
    *,
    suite: str,
    profile_name: str,
    coverage: str | None = None,
    scenario_id: str | None = None,
) -> list[ScenarioDefinition]:
    """Return ordered scenarios matching suite, profile, and optional coverage."""
    if scenario_id is not None:
        definition = SCENARIOS.get(scenario_id)
        if definition is None:
            raise KeyError(f"Unknown scenario id: {scenario_id!r}")
        if profile_name not in definition.allowed_profiles:
            raise RuntimeError(
                f"Scenario {scenario_id!r} does not allow profile {profile_name!r}"
            )
        if suite not in definition.suite_tags and normalize_suite_name(suite) not in {
            normalize_suite_name(tag) for tag in definition.suite_tags
        }:
            # Allow explicit scenario_id even when suite tag differs (pytest single-scenario).
            pass
        return [definition]

    effective_suite = normalize_suite_name(suite)
    selected: list[ScenarioDefinition] = []
    for definition in SCENARIOS.values():
        tags = {normalize_suite_name(tag) for tag in definition.suite_tags}
        if effective_suite not in tags and suite not in definition.suite_tags:
            continue
        if profile_name not in definition.allowed_profiles:
            continue
        if definition.coverage is not None:
            if coverage is None or definition.coverage.upper() != coverage.upper():
                continue
        selected.append(definition)

    return sorted(selected, key=lambda item: item.order)


def scenarios_requiring_probe(probe: str) -> list[ScenarioDefinition]:
    """Return scenarios that declare ``probe`` in required_probes."""
    return [
        definition
        for definition in SCENARIOS.values()
        if probe in definition.required_probes
    ]
