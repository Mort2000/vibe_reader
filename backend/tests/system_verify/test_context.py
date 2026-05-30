"""Unit tests for ScenarioContext and legacy dict adaptation."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.system_verify.core.context import (
    ScenarioContext,
    as_legacy_dict,
    create_scenario_context,
    merge_suite_ctx,
    publish_suite_ctx,
    scenario_context_from_legacy_dict,
    sync_from_legacy_dict,
)
from tests.system_verify.core.run_spec import RunSpec
from tests.system_verify.profiles.registry import profile_from_param_set
from tests.system_verify.core.scenario import ScenarioBuilder, ScenarioRunner
from tests.system_verify.flows.reading import ReadingTrace


def _mock_config() -> MagicMock:
    config = MagicMock()
    config.params.name = "mvp"
    config.params.llm_mode = "stub"
    config.params.long_flow.min_comment_windows = 1
    config.params.long_flow.reading_stop_mode = "comment_windows"
    config.params.budget = MagicMock()
    config.params.assertions = MagicMock()
    config.params.aimock_profile = None
    return config


def test_create_scenario_context_sets_core_fields() -> None:
    config = _mock_config()
    run_manager = MagicMock()
    metrics = MagicMock()
    corpus = MagicMock()

    ctx = create_scenario_context(
        run_manager=run_manager,
        config=config,
        metrics=metrics,
        corpus=corpus,
        scenario_id="S2_continuous_reading",
    )

    assert ctx.run_manager is run_manager
    assert ctx.config is config
    assert ctx.metrics is metrics
    assert ctx.corpus is corpus
    assert ctx.scenario_id == "S2_continuous_reading"
    assert ctx.profile is not None
    assert isinstance(ctx.reading_trace, ReadingTrace)


def test_as_legacy_dict_includes_typed_fields() -> None:
    config = _mock_config()
    ctx = create_scenario_context(
        run_manager=MagicMock(),
        config=config,
        metrics=MagicMock(),
        corpus=MagicMock(),
        scenario_id="S2_continuous_reading",
    )
    ctx.book_id = 42
    ctx.book = {"id": 42, "title": "Test"}
    ctx.chapter_idx = 3
    ctx.comments = [{"id": 1}]
    ctx.extras["probe"] = {"name": "early"}

    legacy = as_legacy_dict(ctx)

    assert legacy["run_manager"] is ctx.run_manager
    assert legacy["config"] is ctx.config
    assert legacy["metrics"] is ctx.metrics
    assert legacy["corpus"] is ctx.corpus
    assert legacy["scenario_id"] == "S2_continuous_reading"
    assert legacy["book_id"] == 42
    assert legacy["book"]["title"] == "Test"
    assert legacy["chapter_idx"] == 3
    assert legacy["comments"] == [{"id": 1}]
    assert legacy["probe"] == {"name": "early"}


def test_sync_from_legacy_dict_updates_typed_fields() -> None:
    config = _mock_config()
    ctx = create_scenario_context(
        run_manager=MagicMock(),
        config=config,
        metrics=MagicMock(),
        corpus=MagicMock(),
        scenario_id="S2_continuous_reading",
    )
    legacy = {
        "book_id": 7,
        "book": {"id": 7},
        "chapter_idx": 1,
        "comments": [{"id": 99}],
        "completed_window": {"status": "done"},
        "reading_session": object(),
    }

    sync_from_legacy_dict(ctx, legacy)

    assert ctx.book_id == 7
    assert ctx.book == {"id": 7}
    assert ctx.chapter_idx == 1
    assert ctx.comments == [{"id": 99}]
    assert ctx.completed_window == {"status": "done"}
    assert ctx.reading_session is legacy["reading_session"]


def test_legacy_roundtrip_preserves_shared_objects() -> None:
    config = _mock_config()
    run_manager = MagicMock()
    metrics = MagicMock()
    ctx = create_scenario_context(
        run_manager=run_manager,
        config=config,
        metrics=metrics,
        corpus=MagicMock(),
        scenario_id="S0_connectivity",
    )
    trace = ctx.reading_trace
    trace.window_done_count = 5

    legacy = as_legacy_dict(ctx)
    legacy["book_id"] = 10
    legacy["custom_key"] = "value"
    sync_from_legacy_dict(ctx, legacy)

    assert ctx.book_id == 10
    assert ctx.extras["custom_key"] == "value"
    assert ctx.reading_trace is trace
    assert ctx.reading_trace.window_done_count == 5


def test_merge_and_publish_suite_ctx() -> None:
    config = _mock_config()
    ctx = create_scenario_context(
        run_manager=MagicMock(),
        config=config,
        metrics=MagicMock(),
        corpus=MagicMock(),
        scenario_id="S2_continuous_reading",
    )
    suite_ctx: dict[str, Any] = {
        "imported_book": {"id": 1},
        "chapters": [{"chapter_idx": 0}],
    }

    merge_suite_ctx(ctx, suite_ctx)

    legacy = as_legacy_dict(ctx)
    assert legacy["imported_book"] == {"id": 1}
    assert legacy["chapters"] == [{"chapter_idx": 0}]

    ctx.comment_audit_exporter = "exporter"
    publish_suite_ctx(ctx, suite_ctx)
    assert suite_ctx["comment_audit_exporter"] == "exporter"
    assert suite_ctx["reading_trace"] is ctx.reading_trace


def test_scenario_context_from_legacy_dict() -> None:
    config = _mock_config()
    run_manager = MagicMock()
    metrics = MagicMock()
    trace = ReadingTrace()
    legacy = {
        "run_manager": run_manager,
        "config": config,
        "metrics": metrics,
        "corpus": MagicMock(),
        "scenario_id": "S1_book_import",
        "reading_trace": trace,
        "book_id": 3,
    }

    ctx = scenario_context_from_legacy_dict(legacy)

    assert ctx.run_manager is run_manager
    assert ctx.reading_trace is trace
    assert ctx.book_id == 3


@pytest.mark.asyncio
async def test_scenario_runner_accepts_typed_context_steps() -> None:
    config = _mock_config()
    run_manager = MagicMock()
    observed: list[str] = []

    async def typed_step(ctx: ScenarioContext) -> None:
        observed.append(ctx.scenario_id)
        ctx.book_id = 99

    builder = ScenarioBuilder("typed_scenario", "typed steps")
    builder.add_step("typed", "typed step", typed_step)

    ctx = create_scenario_context(
        run_manager=run_manager,
        config=config,
        metrics=MagicMock(),
        scenario_id="typed_scenario",
    )
    runner = ScenarioRunner(run_manager, config)
    result = await runner.run(builder, context=ctx)

    assert result.status.value == "passed"
    assert observed == ["typed_scenario"]
    assert ctx.book_id == 99


@pytest.mark.asyncio
async def test_scenario_runner_supports_legacy_dict_steps() -> None:
    config = _mock_config()
    run_manager = MagicMock()
    captured: dict[str, Any] = {}

    async def legacy_step(ctx: dict[str, Any]) -> None:
        captured["scenario_id"] = ctx["scenario_id"]
        ctx["book_id"] = 55

    builder = ScenarioBuilder("legacy_scenario", "legacy steps")
    builder.add_step("legacy", "legacy step", legacy_step)

    ctx = create_scenario_context(
        run_manager=run_manager,
        config=config,
        metrics=MagicMock(),
        scenario_id="legacy_scenario",
    )
    runner = ScenarioRunner(run_manager, config)
    result = await runner.run(builder, context=ctx)

    assert result.status.value == "passed"
    assert captured["scenario_id"] == "legacy_scenario"
    assert ctx.book_id == 55


def test_create_scenario_context_with_spec_and_profile() -> None:
    config = _mock_config()
    spec = RunSpec(
        suite="mvp",
        scenario_id="S2_continuous_reading",
        profile_name="mvp_stub",
        param_set_name="mvp",
        llm_mode="stub",
        coverage=None,
        target_url="http://127.0.0.1:8000",
        corpus_path="tests/corpus/manifest.toml",
        config_path="tests/corpus/verify.toml",
    )
    profile = profile_from_param_set(config.params)

    ctx = create_scenario_context(
        run_manager=MagicMock(),
        config=config,
        metrics=MagicMock(),
        scenario_id="S2_continuous_reading",
        spec=spec,
        profile=profile,
    )

    assert ctx.spec is spec
    assert ctx.profile is profile
    legacy = as_legacy_dict(ctx)
    assert legacy["spec"] is spec
    assert legacy["profile"] is profile


def test_as_legacy_dict_maps_reading_cursor_alias() -> None:
    config = _mock_config()
    ctx = create_scenario_context(
        run_manager=MagicMock(),
        config=config,
        metrics=MagicMock(),
        scenario_id="S4_long_context",
    )
    cursor = object()
    ctx.cursor = cursor

    legacy = as_legacy_dict(ctx)

    assert legacy["cursor"] is cursor
    assert legacy["reading_cursor"] is cursor


def test_sync_from_legacy_dict_prefers_reading_cursor() -> None:
    config = _mock_config()
    ctx = create_scenario_context(
        run_manager=MagicMock(),
        config=config,
        metrics=MagicMock(),
        scenario_id="R1_real_happy_path",
    )
    reading_cursor = object()
    sync_from_legacy_dict(ctx, {"reading_cursor": reading_cursor, "cursor": object()})

    assert ctx.cursor is reading_cursor


def test_typed_phase22_fields_roundtrip_in_legacy_dict() -> None:
    config = _mock_config()
    ctx = create_scenario_context(
        run_manager=MagicMock(),
        config=config,
        metrics=MagicMock(),
        scenario_id="S2_continuous_reading",
    )
    ctx.comment_audit_exporter = object()
    ctx.compaction_audit_exporter = object()
    ctx.verify_jobs = [{"id": 1}]
    ctx.verify_runtime = {"verify_mode": True}
    ctx.last_progress_response = {"chapter_idx": 2}
    ctx.comments_before_jump_back = {3: 99}
    ctx.comment_event_count_before_jump_back = 4

    legacy = as_legacy_dict(ctx)
    fresh = create_scenario_context(
        run_manager=MagicMock(),
        config=config,
        metrics=MagicMock(),
        scenario_id="S2_continuous_reading",
    )
    sync_from_legacy_dict(fresh, legacy)

    assert fresh.comment_audit_exporter is ctx.comment_audit_exporter
    assert fresh.compaction_audit_exporter is ctx.compaction_audit_exporter
    assert fresh.verify_jobs == [{"id": 1}]
    assert fresh.verify_runtime == {"verify_mode": True}
    assert fresh.last_progress_response == {"chapter_idx": 2}
    assert fresh.comments_before_jump_back == {3: 99}
    assert fresh.comment_event_count_before_jump_back == 4


@pytest.mark.asyncio
async def test_scenario_runner_syncs_mutations_back_to_input_dict() -> None:
    config = _mock_config()
    run_manager = MagicMock()
    session = object()

    async def legacy_step(ctx: dict[str, Any]) -> None:
        ctx["reading_session"] = session
        ctx["book_id"] = 77
        ctx["imported_book"] = {"id": 77}

    builder = ScenarioBuilder("dict_sync", "sync dict after run")
    builder.add_step("legacy", "legacy step", legacy_step)

    ctx: dict[str, Any] = {
        "run_manager": run_manager,
        "config": config,
        "metrics": MagicMock(),
        "scenario_id": "dict_sync",
        "reading_trace": ReadingTrace(),
    }
    runner = ScenarioRunner(run_manager, config)
    result = await runner.run(builder, context=ctx)

    assert result.status.value == "passed"
    assert ctx["reading_session"] is session
    assert ctx["book_id"] == 77
    assert ctx["imported_book"] == {"id": 77}


@pytest.mark.asyncio
async def test_scenario_runner_run_none_raises_type_error() -> None:
    config = _mock_config()
    run_manager = MagicMock()

    async def noop_step(ctx: dict[str, Any]) -> None:
        return None

    builder = ScenarioBuilder("none_ctx", "missing context")
    builder.add_step("noop", "noop", noop_step)

    runner = ScenarioRunner(run_manager, config)
    with pytest.raises(TypeError, match="requires a scenario context"):
        await runner.run(builder, context=None)
