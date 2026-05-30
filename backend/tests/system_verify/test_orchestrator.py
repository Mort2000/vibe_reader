from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.system_verify.core.orchestrator import (
    VerifySessionHandle,
    run_scenario,
    run_scenarios,
    run_suite_from_spec,
    run_suite_scenarios,
)
from tests.system_verify.core.run_spec import RunSpec, ScenarioDefinition


@dataclass
class _FakeCorpus:
    token: str


async def _noop_invoke(_handle: Any) -> None:
    return None


async def _record_corpus_token(handle: VerifySessionHandle) -> None:
    assert handle.corpus is not None
    handle.suite_ctx["corpus_token"] = handle.corpus.token


def _scenario(
    scenario_id: str,
    *,
    requires_corpus: bool,
    invoke,
    order: int = 0,
) -> ScenarioDefinition:
    return ScenarioDefinition(
        id=scenario_id,
        suite_tags=frozenset({"mvp"}),
        allowed_profiles=frozenset({"mvp_stub"}),
        required_probes=(),
        order=order,
        requires_corpus=requires_corpus,
        invoke=invoke,
    )


def _handle(*, corpus: Any | None = None) -> VerifySessionHandle:
    config = MagicMock()
    config.params.name = "mvp"
    return VerifySessionHandle(
        spec=RunSpec(
            suite="mvp",
            scenario_id=None,
            profile_name="mvp_stub",
            param_set_name="mvp",
            llm_mode="stub",
            coverage=None,
            target_url="http://127.0.0.1:8000",
            corpus_path="tests/corpus/manifest.toml",
            config_path="tests/corpus/verify.toml",
        ),
        config=config,
        run_manager=MagicMock(),
        metrics=MagicMock(),
        corpus_path="tests/corpus/manifest.toml",
        suite_ctx={},
        corpus=corpus,
    )


@pytest.mark.asyncio
async def test_run_scenarios_prepares_corpus_once_for_mvp_chain() -> None:
    prepare_calls: list[str] = []

    def fake_prepare(_mgr, _config, corpus_path: str) -> _FakeCorpus:
        prepare_calls.append(corpus_path)
        return _FakeCorpus(token="shared")

    scenarios = [
        _scenario("S0_connectivity", requires_corpus=False, invoke=_noop_invoke, order=0),
        _scenario("S1_book_import", requires_corpus=True, invoke=_record_corpus_token, order=10),
        _scenario("S2_continuous_reading", requires_corpus=True, invoke=_record_corpus_token, order=20),
    ]

    with patch(
        "tests.system_verify.core.orchestrator.prepare_corpus",
        side_effect=fake_prepare,
    ):
        await run_scenarios(_handle(), scenarios)

    assert prepare_calls == ["tests/corpus/manifest.toml"]


@pytest.mark.asyncio
async def test_run_scenario_returns_handle_with_corpus() -> None:
    fake_corpus = _FakeCorpus(token="once")

    with patch(
        "tests.system_verify.core.orchestrator.prepare_corpus",
        return_value=fake_corpus,
    ):
        updated = await run_scenario(
            _handle(),
            _scenario("S1_book_import", requires_corpus=True, invoke=_noop_invoke),
        )

    assert updated.corpus is fake_corpus


def test_orchestrator_import_without_cycle() -> None:
    """Lazy prepare_corpus import must not create an import cycle at load time."""
    import tests.system_verify.core.orchestrator as orchestrator  # noqa: F401

    assert orchestrator.prepare_corpus is not None
    assert orchestrator.run_suite_scenarios is not None


def _mvp_config(*, llm_mode: str = "stub", param_name: str = "mvp") -> MagicMock:
    config = MagicMock()
    config.params.name = param_name
    config.params.llm_mode = llm_mode
    config.run.suite = "mvp"
    config.target.base_url = "http://127.0.0.1:8000"
    config.is_real_llm = llm_mode == "real"
    return config


@pytest.mark.asyncio
async def test_run_scenario_rejects_missing_invoke() -> None:
    scenario = _scenario("S0_connectivity", requires_corpus=False, invoke=None)

    with pytest.raises(RuntimeError, match="has no invoke handler"):
        await run_scenario(_handle(), scenario)


@pytest.mark.asyncio
async def test_run_scenario_rejects_disallowed_profile() -> None:
    handle = _handle()
    handle.config.params.name = "r1_a2_stub"

    with pytest.raises(RuntimeError, match="does not allow profile"):
        await run_scenario(
            handle,
            _scenario("S0_connectivity", requires_corpus=False, invoke=_noop_invoke),
        )


@pytest.mark.asyncio
async def test_run_suite_scenarios_rejects_non_stub_for_mvp() -> None:
    config = _mvp_config(llm_mode="real", param_name="r1_a2_real")

    with pytest.raises(RuntimeError, match="mvp/smoke suites require a stub param set"):
        await run_suite_scenarios(
            MagicMock(),
            config,
            MagicMock(),
            "tests/corpus/manifest.toml",
            suite="mvp",
        )


@pytest.mark.asyncio
async def test_run_suite_scenarios_rejects_invalid_real_happy_path_param_set() -> None:
    config = _mvp_config(param_name="mvp")

    with pytest.raises(RuntimeError, match="real-happy-path requires a matching param set"):
        await run_suite_scenarios(
            MagicMock(),
            config,
            MagicMock(),
            "tests/corpus/manifest.toml",
            suite="real-happy-path",
        )


@pytest.mark.asyncio
async def test_run_suite_scenarios_rejects_invalid_real_llm_config() -> None:
    config = _mvp_config(llm_mode="real", param_name="r1_a2_real")
    config.is_real_llm = True

    with patch(
        "tests.system_verify.core.orchestrator.validate_real_llm_config",
        return_value=["real_llm.base_url is required when llm.mode=real"],
    ):
        with pytest.raises(RuntimeError, match="Real LLM config invalid"):
            await run_suite_scenarios(
                MagicMock(),
                config,
                MagicMock(),
                "tests/corpus/manifest.toml",
                suite="real-happy-path",
                coverage="A2",
            )


@pytest.mark.asyncio
async def test_run_suite_scenarios_rejects_coverage_param_set_mismatch() -> None:
    config = _mvp_config(param_name="r1_a3_stub")

    with pytest.raises(RuntimeError, match="requires param set r1_a2_"):
        await run_suite_scenarios(
            MagicMock(),
            config,
            MagicMock(),
            "tests/corpus/manifest.toml",
            suite="real-happy-path",
            coverage="A2",
        )


@pytest.mark.asyncio
async def test_run_suite_scenarios_rejects_empty_scenario_selection() -> None:
    config = _mvp_config()

    with patch(
        "tests.system_verify.core.orchestrator.select_scenarios_for_run",
        return_value=[],
    ):
        with pytest.raises(RuntimeError, match="No scenarios registered"):
            await run_suite_scenarios(
                MagicMock(),
                config,
                MagicMock(),
                "tests/corpus/manifest.toml",
                suite="mvp",
            )


@pytest.mark.asyncio
async def test_run_suite_scenarios_delegates_to_run_scenarios() -> None:
    config = _mvp_config()
    selected = [
        _scenario("S0_connectivity", requires_corpus=False, invoke=_noop_invoke, order=0),
    ]
    run_calls: list[list[ScenarioDefinition]] = []

    async def fake_run_scenarios(_handle: VerifySessionHandle, scenarios: list[ScenarioDefinition]) -> None:
        run_calls.append(scenarios)

    with patch(
        "tests.system_verify.core.orchestrator.select_scenarios_for_run",
        return_value=selected,
    ), patch(
        "tests.system_verify.core.orchestrator.run_scenarios",
        side_effect=fake_run_scenarios,
    ):
        await run_suite_scenarios(
            MagicMock(),
            config,
            MagicMock(),
            "tests/corpus/manifest.toml",
            suite="mvp",
        )

    assert len(run_calls) == 1
    assert [s.id for s in run_calls[0]] == ["S0_connectivity"]


@pytest.mark.asyncio
async def test_run_suite_from_spec_finishes_run_manager() -> None:
    spec = RunSpec(
        suite="mvp",
        scenario_id=None,
        profile_name="mvp_stub",
        param_set_name="mvp",
        llm_mode="stub",
        coverage=None,
        target_url="http://127.0.0.1:8000",
        corpus_path="tests/corpus/manifest.toml",
        config_path="tests/corpus/verify.toml",
    )
    mgr = MagicMock()

    with patch(
        "tests.system_verify.core.orchestrator.build_verify_config_from_run_spec",
        return_value=_mvp_config(),
    ), patch(
        "tests.system_verify.core.orchestrator.resolve_profile_for_run_spec",
        return_value=MagicMock(name="mvp_stub"),
    ), patch(
        "tests.system_verify.core.orchestrator.RunManager",
        return_value=mgr,
    ), patch(
        "tests.system_verify.core.orchestrator.run_suite_scenarios",
    ) as run_suite:
        await run_suite_from_spec(spec)

    mgr.start.assert_called_once()
    mgr.finish.assert_called_once()
    mgr.write_manifest.assert_called_once()
    run_suite.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_suite_from_spec_finishes_run_manager_on_failure() -> None:
    spec = RunSpec(
        suite="mvp",
        scenario_id=None,
        profile_name="mvp_stub",
        param_set_name="mvp",
        llm_mode="stub",
        coverage=None,
        target_url="http://127.0.0.1:8000",
        corpus_path="tests/corpus/manifest.toml",
        config_path="tests/corpus/verify.toml",
    )
    mgr = MagicMock()

    with patch(
        "tests.system_verify.core.orchestrator.build_verify_config_from_run_spec",
        return_value=_mvp_config(),
    ), patch(
        "tests.system_verify.core.orchestrator.resolve_profile_for_run_spec",
        return_value=MagicMock(name="mvp_stub"),
    ), patch(
        "tests.system_verify.core.orchestrator.RunManager",
        return_value=mgr,
    ), patch(
        "tests.system_verify.core.orchestrator.run_suite_scenarios",
        side_effect=RuntimeError("scenario failed"),
    ):
        with pytest.raises(RuntimeError, match="scenario failed"):
            await run_suite_from_spec(spec)

    mgr.start.assert_called_once()
    mgr.finish.assert_called_once()
    mgr.write_manifest.assert_called_once()
