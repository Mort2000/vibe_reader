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

Pre/post data directory reset is handled by the vibe-verify CLI run command;
pytest fixtures do not manage backend process lifecycle unless ``--spawn-backend``.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from typing import Any

import pytest

from .config import VerifyConfig, load_verify_config
from .corpus import CorpusManager
from .env_file import load_project_dotenv
from .metrics_collector import MetricsAggregator
from .run import RunManager
from .suite import finalize_reports


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
def verify_config(request: pytest.FixtureRequest) -> VerifyConfig:
    param_set = request.config.getoption("--param-set")
    llm_mode = request.config.getoption("--llm-mode")
    return load_verify_config(
        param_set=param_set,
        llm_mode_override=llm_mode,
    )


@pytest.fixture(scope="session")
def aimock_sidecar(verify_config: VerifyConfig, request: pytest.FixtureRequest):
    """Start AIMock sidecar for stub-mode pytest sessions."""
    if verify_config.is_real_llm or not verify_config.llm_stub.aimock.enabled:
        yield None
        return

    from .llm_stub.aimock_launcher import AIMockSidecar
    from .llm_stub.env import (
        BackendProcess,
        assert_backend_stub_llm_ready,
        inject_stub_backend_env,
        print_stub_backend_env_notice,
        spawn_backend,
    )

    backend_proc: BackendProcess | None = None
    spawn = request.config.getoption("--spawn-backend")

    with AIMockSidecar(verify_config) as session:
        inject_stub_backend_env(session, verify_config)
        print_stub_backend_env_notice(session, verify_config)
        if spawn:
            backend_proc = spawn_backend(verify_config, session)
        else:
            assert_backend_stub_llm_ready(verify_config, session)
        yield session
        if backend_proc is not None:
            backend_proc.stop()


@pytest.fixture(scope="session")
def run_manager(
    verify_config: VerifyConfig,
    request: pytest.FixtureRequest,
    aimock_sidecar,
) -> RunManager:
    run_id = request.config.getoption("--verify-run-id") or os.environ.get(
        "VIBE_READER_VERIFY_RUN_ID"
    )
    mgr = RunManager(verify_config, run_id=run_id or None)
    if aimock_sidecar is not None:
        mgr.set_aimock_info(
            {
                "provider": "aimock",
                "version": aimock_sidecar.version,
                "base_url": aimock_sidecar.base_url,
                "fixture_hash": aimock_sidecar.fixture_hash,
                "profile_hash": aimock_sidecar.profile_hash,
                "strict": aimock_sidecar.strict,
                "profile": aimock_sidecar.profile,
            }
        )
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


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
