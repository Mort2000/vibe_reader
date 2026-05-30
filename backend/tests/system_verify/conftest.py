"""Pytest fixtures and markers for system verification tests.

On startup, loads the first ``.env`` found under the current working directory,
``backend/``, or the repository root (see ``.env.example``). Shell environment
variables already set take precedence.

Stub mode: the ``aimock_sidecar`` fixture starts AIMock and calls
``inject_stub_backend_env`` so the verify runner publishes the required LLM env.
The backend is still a separate process — restart it with the printed env, or
pass ``--spawn-backend`` to pytest / ``vibe-verify run``.

When running pytest scenarios against a live backend, start it with an isolated
data directory and verify mode, for example:

    VIBE_READER_DATA_DIR=/tmp/vibe_reader_verify \\
    VIBE_READER_VERIFY_MODE=1 \\
    VIBE_READER_LLM_BASE_URL=http://127.0.0.1:4010/v1 \\
    VIBE_READER_LLM_API_KEY=aimock-test-key \\
    python3 -m app.main

With ``--spawn-backend``, the ``run_manager`` fixture resets backend data and
syncs verify.toml app config before scenarios (same as CLI pre-run lifecycle).

When backend / verify mode / stub LLM / corpus prerequisites are missing,
``test_scenarios.py`` skips via the ``require_integration_ready`` fixture
instead of failing after long scenario runs. Use ``--require-integration`` to
fail fast in CI that expects a live backend.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from typing import Any

import pytest

from .core.config import VerifyConfig
from .core.config_loader import apply_param_set
from .core.run_spec import (
    RunSpec,
    build_verify_config_from_run_spec,
    resolve_run_spec_from_pytest,
    run_spec_for_param_set,
)
from .corpus import CorpusManager
from .env_file import load_project_dotenv
from .core.orchestrator import VerifySessionHandle, build_session_handle
from .metrics_collector import MetricsAggregator
from .core.run_manager import RunManager
from .core.orchestrator import finalize_reports


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--llm-mode",
        action="store",
        default=None,
        choices=["stub", "real"],
        help="Override verification LLM mode (must match param set)",
    )
    parser.addoption(
        "--param-set",
        action="store",
        default=None,
        help="Named verification param set",
    )
    parser.addoption(
        "--verify-run-id",
        action="store",
        default=None,
        help="Reuse an existing verification run id",
    )
    parser.addoption(
        "--spawn-backend",
        action="store_true",
        default=False,
        help="Spawn backend subprocess with stub LLM env (stub mode only)",
    )
    parser.addoption(
        "--require-integration",
        action="store_true",
        default=False,
        help="Fail instead of skip when integration prerequisites are not met",
    )


def pytest_configure(config: pytest.Config) -> None:
    load_project_dotenv()
    config.addinivalue_line(
        "markers",
        "system: default stub-based system verification scenarios",
    )
    config.addinivalue_line(
        "markers",
        "real_llm: real LLM happy path scenarios (explicit opt-in)",
    )
    config.addinivalue_line("markers", "system_verify: all system verification tests")


@pytest.fixture(scope="session")
def verify_run_spec(request: pytest.FixtureRequest) -> RunSpec:
    """Immutable run specification for the pytest session."""
    return resolve_run_spec_from_pytest(request)


@pytest.fixture(scope="session")
def verify_config(verify_run_spec: RunSpec) -> VerifyConfig:
    return build_verify_config_from_run_spec(verify_run_spec)


@pytest.fixture(scope="session")
def verify_run_spec_r1_a2_stub(request: pytest.FixtureRequest) -> RunSpec:
    return run_spec_for_param_set(
        "r1_a2_stub",
        suite="real-happy-path",
        coverage="A2",
        llm_mode_override=request.config.getoption("--llm-mode"),
    )


@pytest.fixture(scope="session")
def verify_config_r1_a2_stub(verify_run_spec_r1_a2_stub: RunSpec) -> VerifyConfig:
    return build_verify_config_from_run_spec(verify_run_spec_r1_a2_stub)


@pytest.fixture(scope="session")
def verify_run_spec_r1_a2_real(request: pytest.FixtureRequest) -> RunSpec:
    return run_spec_for_param_set(
        "r1_a2_real",
        suite="real-happy-path",
        coverage="A2",
        llm_mode_override=request.config.getoption("--llm-mode"),
    )


@pytest.fixture(scope="session")
def verify_config_r1_a2_real(verify_run_spec_r1_a2_real: RunSpec) -> VerifyConfig:
    return build_verify_config_from_run_spec(verify_run_spec_r1_a2_real)


@pytest.fixture(scope="session")
def verify_run_spec_r1_a3_stub(request: pytest.FixtureRequest) -> RunSpec:
    return run_spec_for_param_set(
        "r1_a3_stub",
        suite="real-happy-path",
        coverage="A3",
        llm_mode_override=request.config.getoption("--llm-mode"),
    )


@pytest.fixture(scope="session")
def verify_config_r1_a3_stub(verify_run_spec_r1_a3_stub: RunSpec) -> VerifyConfig:
    return build_verify_config_from_run_spec(verify_run_spec_r1_a3_stub)


@pytest.fixture(scope="session")
def verify_run_spec_r1_a3_real(request: pytest.FixtureRequest) -> RunSpec:
    return run_spec_for_param_set(
        "r1_a3_real",
        suite="real-happy-path",
        coverage="A3",
        llm_mode_override=request.config.getoption("--llm-mode"),
    )


@pytest.fixture(scope="session")
def verify_config_r1_a3_real(verify_run_spec_r1_a3_real: RunSpec) -> VerifyConfig:
    return build_verify_config_from_run_spec(verify_run_spec_r1_a3_real)


@pytest.fixture(scope="session")
def mode_environment_handle(
    verify_run_spec: RunSpec,
    verify_config: VerifyConfig,
    request: pytest.FixtureRequest,
):
    """Prepare stub/real mode environment for the pytest session."""
    from .core.run_spec import resolve_profile_for_run_spec
    from .modes.base import cleanup_mode, prepare_mode, resolve_mode_environment

    profile = resolve_profile_for_run_spec(verify_run_spec)
    env = resolve_mode_environment(verify_run_spec)
    handle = prepare_mode(
        env,
        verify_run_spec,
        profile,
        config=verify_config,
        spawn_backend=request.config.getoption("--spawn-backend"),
    )
    yield handle
    cleanup_mode(env, handle)


@pytest.fixture(scope="session")
def aimock_sidecar(mode_environment_handle):
    """AIMock session from the active mode environment (stub mode only)."""
    return mode_environment_handle.aimock_session


@pytest.fixture(scope="session")
def require_integration_ready(
    verify_config: VerifyConfig,
    corpus_manager: CorpusManager,
    aimock_sidecar,
    request: pytest.FixtureRequest,
) -> None:
    """Skip (or fail with --require-integration) when live backend prerequisites missing."""
    from .integration_prerequisites import check_integration_prerequisites

    issues = check_integration_prerequisites(
        verify_config,
        corpus_manager,
        aimock_session=aimock_sidecar,
    )
    if not issues:
        return
    message = "Integration prerequisites not met: " + "; ".join(issues)
    if request.config.getoption("--require-integration"):
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def verify_session(
    verify_run_spec: RunSpec,
    verify_config: VerifyConfig,
    run_manager: RunManager,
    metrics: MetricsAggregator,
    corpus_manager: CorpusManager,
    suite_ctx: dict[str, Any],
) -> VerifySessionHandle:
    """Orchestrator session for default MVP pytest runs."""
    return build_session_handle(
        spec=verify_run_spec,
        config=verify_config,
        run_manager=run_manager,
        metrics=metrics,
        corpus_path=str(pathlib.Path("tests/corpus/manifest.toml")),
        suite_ctx=suite_ctx,
        corpus=corpus_manager,
    )


@pytest.fixture(scope="session")
def verify_session_r1_a2_stub(
    verify_run_spec_r1_a2_stub: RunSpec,
    verify_config: VerifyConfig,
    run_manager: RunManager,
    metrics: MetricsAggregator,
    corpus_manager: CorpusManager,
    suite_ctx: dict[str, Any],
) -> VerifySessionHandle:
    apply_param_set(verify_config, verify_run_spec_r1_a2_stub.param_set_name)
    return build_session_handle(
        spec=verify_run_spec_r1_a2_stub,
        config=verify_config,
        run_manager=run_manager,
        metrics=metrics,
        corpus_path=str(pathlib.Path("tests/corpus/manifest.toml")),
        suite_ctx=suite_ctx,
        corpus=corpus_manager,
    )


@pytest.fixture(scope="session")
def verify_session_r1_a2_real(
    verify_run_spec_r1_a2_real: RunSpec,
    verify_config: VerifyConfig,
    run_manager: RunManager,
    metrics: MetricsAggregator,
    corpus_manager: CorpusManager,
    suite_ctx: dict[str, Any],
) -> VerifySessionHandle:
    apply_param_set(verify_config, verify_run_spec_r1_a2_real.param_set_name)
    return build_session_handle(
        spec=verify_run_spec_r1_a2_real,
        config=verify_config,
        run_manager=run_manager,
        metrics=metrics,
        corpus_path=str(pathlib.Path("tests/corpus/manifest.toml")),
        suite_ctx=suite_ctx,
        corpus=corpus_manager,
    )


@pytest.fixture(scope="session")
def verify_session_r1_a3_stub(
    verify_run_spec_r1_a3_stub: RunSpec,
    verify_config: VerifyConfig,
    run_manager: RunManager,
    metrics: MetricsAggregator,
    corpus_manager: CorpusManager,
    suite_ctx: dict[str, Any],
) -> VerifySessionHandle:
    apply_param_set(verify_config, verify_run_spec_r1_a3_stub.param_set_name)
    return build_session_handle(
        spec=verify_run_spec_r1_a3_stub,
        config=verify_config,
        run_manager=run_manager,
        metrics=metrics,
        corpus_path=str(pathlib.Path("tests/corpus/manifest.toml")),
        suite_ctx=suite_ctx,
        corpus=corpus_manager,
    )


@pytest.fixture(scope="session")
def verify_session_r1_a3_real(
    verify_run_spec_r1_a3_real: RunSpec,
    verify_config: VerifyConfig,
    run_manager: RunManager,
    metrics: MetricsAggregator,
    corpus_manager: CorpusManager,
    suite_ctx: dict[str, Any],
) -> VerifySessionHandle:
    apply_param_set(verify_config, verify_run_spec_r1_a3_real.param_set_name)
    return build_session_handle(
        spec=verify_run_spec_r1_a3_real,
        config=verify_config,
        run_manager=run_manager,
        metrics=metrics,
        corpus_path=str(pathlib.Path("tests/corpus/manifest.toml")),
        suite_ctx=suite_ctx,
        corpus=corpus_manager,
    )


@pytest.fixture(scope="session")
def run_manager(
    verify_config: VerifyConfig,
    request: pytest.FixtureRequest,
    mode_environment_handle,
) -> RunManager:
    run_id = request.config.getoption("--verify-run-id") or os.environ.get(
        "VIBE_READER_VERIFY_RUN_ID"
    )
    mgr = RunManager(verify_config, run_id=run_id or None)
    if mode_environment_handle.manifest_info.get("provider") == "aimock":
        mgr.set_aimock_info(mode_environment_handle.manifest_info)
    mgr.start()
    yield mgr
    metrics = MetricsAggregator(mgr, verify_config)
    findings = metrics.check_no_api_key_in_outputs()
    mgr.set_security_checks(
        {
            "api_key_leak_scan": {
                "passed": len(findings) == 0,
                "findings_count": len(findings),
                "findings": findings,
            },
        }
    )
    mgr.finish()
    mgr.write_manifest()
    finalize_reports(mgr)


@pytest.fixture(scope="session")
def metrics(run_manager: RunManager, verify_config: VerifyConfig) -> MetricsAggregator:
    return MetricsAggregator(run_manager, verify_config)


@pytest.fixture(scope="session")
def corpus_manager(verify_config: VerifyConfig) -> CorpusManager:
    manifest_path = pathlib.Path("tests/corpus/manifest.toml")
    cm = CorpusManager(verify_config, manifest_path)
    cm.load()
    return cm


@pytest.fixture(scope="session")
def suite_ctx() -> dict[str, Any]:
    """Shared book/import state when running S1→S2→S3 in one pytest session."""
    return {}


@pytest.fixture
def reset_verify_data_before_scenario(
    request: pytest.FixtureRequest,
    verify_config: VerifyConfig,
    run_manager: RunManager,
    suite_ctx: dict[str, Any],
) -> None:
    """Reset backend data and sync app config before each integration scenario."""
    if not request.config.getoption("--spawn-backend"):
        return
    from .data_lifecycle import prepare_run_data_dir

    asyncio.run(prepare_run_data_dir(verify_config, run_manager, phase="pre"))
    suite_ctx.clear()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
