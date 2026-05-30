"""Unit tests for stub/real mode environments."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.system_verify.core.run_spec import (
    build_verify_config_from_run_spec,
    resolve_profile_for_run_spec,
    resolve_run_spec,
)
from tests.system_verify.modes.base import (
    ModeHandle,
    cleanup_mode,
    prepare_mode,
    resolve_mode_environment,
    validate_mode_prerequisites,
)
from tests.system_verify.modes.real_llm import RealLLMEnvironment
from tests.system_verify.modes.stub_aimock import StubAIMockEnvironment, aimock_manifest_info


def _stub_spec() -> tuple:
    spec = resolve_run_spec(suite="mvp")
    config = build_verify_config_from_run_spec(spec)
    profile = resolve_profile_for_run_spec(spec)
    return spec, config, profile


def _real_spec() -> tuple:
    spec = resolve_run_spec(
        suite="real-happy-path",
        coverage="A2",
        llm_mode_override="real",
        param_set="r1_a2_real",
    )
    config = build_verify_config_from_run_spec(spec)
    profile = resolve_profile_for_run_spec(spec)
    return spec, config, profile


def test_resolve_mode_environment_stub() -> None:
    spec, _, _ = _stub_spec()
    env = resolve_mode_environment(spec)
    assert isinstance(env, StubAIMockEnvironment)


def test_resolve_mode_environment_real() -> None:
    spec, _, _ = _real_spec()
    env = resolve_mode_environment(spec)
    assert isinstance(env, RealLLMEnvironment)


def test_stub_prepare_skips_when_real_llm() -> None:
    spec, config, profile = _real_spec()
    env = StubAIMockEnvironment()
    handle = prepare_mode(env, spec, profile, config=config)
    assert handle.aimock_session is None
    assert handle.manifest_info == {}


def test_stub_prepare_skips_when_aimock_disabled() -> None:
    spec, config, profile = _stub_spec()
    config.llm_stub.aimock.enabled = False
    env = StubAIMockEnvironment()
    handle = prepare_mode(
        env,
        spec,
        profile,
        config=config,
        print_env_notice=False,
    )
    assert handle.aimock_session is None
    assert handle.manifest_info == {}


def test_stub_prepare_starts_sidecar_and_sets_manifest() -> None:
    spec, config, profile = _stub_spec()
    session = MagicMock(
        version="1.0",
        base_url="http://127.0.0.1:4010/v1",
        fixture_hash="abc",
        profile_hash="def",
        strict=True,
        profile="default",
    )
    sidecar = MagicMock()
    sidecar.__enter__.return_value = session
    env = StubAIMockEnvironment()

    with patch(
        "tests.system_verify.modes.stub_aimock.AIMockSidecar",
        return_value=sidecar,
    ), patch(
        "tests.system_verify.modes.stub_aimock.inject_stub_backend_env",
    ) as inject_env:
        handle = prepare_mode(
            env,
            spec,
            profile,
            config=config,
            print_env_notice=False,
        )

    inject_env.assert_called_once_with(session, config)
    assert handle.aimock_session is session
    assert handle.manifest_info == aimock_manifest_info(session)
    cleanup_mode(env, handle)
    sidecar.__exit__.assert_called_once()


def test_stub_prepare_spawn_backend() -> None:
    spec, config, profile = _stub_spec()
    session = MagicMock(
        version="1.0",
        base_url="http://127.0.0.1:4010/v1",
        fixture_hash="abc",
        profile_hash="def",
        strict=True,
        profile="default",
    )
    sidecar = MagicMock()
    sidecar.__enter__.return_value = session
    backend_proc = MagicMock()
    env = StubAIMockEnvironment()

    with patch(
        "tests.system_verify.modes.stub_aimock.AIMockSidecar",
        return_value=sidecar,
    ), patch(
        "tests.system_verify.modes.stub_aimock.inject_stub_backend_env",
    ), patch(
        "tests.system_verify.modes.stub_aimock.spawn_backend_proc",
        return_value=backend_proc,
    ) as spawn:
        handle = prepare_mode(
            env,
            spec,
            profile,
            config=config,
            spawn_backend=True,
            print_env_notice=False,
        )

    spawn.assert_called_once_with(config, session)
    assert handle._backend_proc is backend_proc
    cleanup_mode(env, handle)
    backend_proc.stop.assert_called_once()


def test_stub_validate_prerequisites_reports_backend_wiring() -> None:
    spec, config, profile = _stub_spec()
    session = MagicMock()
    handle = ModeHandle(
        config=config,
        spec=spec,
        profile=profile,
        aimock_session=session,
    )
    env = StubAIMockEnvironment()
    with patch(
        "tests.system_verify.modes.stub_aimock.validate_backend_stub_llm",
        return_value=["backend llm.api_key_configured is false"],
    ):
        issues = validate_mode_prerequisites(env, handle)
    assert len(issues) == 1
    assert "stub LLM not wired on backend" in issues[0]


def test_stub_validate_prerequisites_empty_when_no_session() -> None:
    spec, config, profile = _stub_spec()
    handle = ModeHandle(config=config, spec=spec, profile=profile)
    env = StubAIMockEnvironment()
    assert validate_mode_prerequisites(env, handle) == []


def test_real_prepare_does_not_start_aimock() -> None:
    spec, config, profile = _real_spec()
    env = RealLLMEnvironment()
    with patch(
        "tests.system_verify.modes.stub_aimock.AIMockSidecar",
    ) as sidecar_cls:
        handle = prepare_mode(env, spec, profile, config=config)
    sidecar_cls.assert_not_called()
    assert handle.manifest_info == {"real_llm": True}


def test_real_validate_prerequisites_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    spec, config, profile = _real_spec()
    monkeypatch.delenv(config.real_llm.api_key_env, raising=False)
    handle = ModeHandle(config=config, spec=spec, profile=profile)
    env = RealLLMEnvironment()
    issues = validate_mode_prerequisites(env, handle)
    assert any("api key" in issue.lower() for issue in issues)


def test_stub_launch_error_exits_when_configured() -> None:
    spec, config, profile = _stub_spec()
    sidecar = MagicMock()
    sidecar.__enter__.side_effect = __import__(
        "tests.system_verify.llm_stub.aimock_launcher",
        fromlist=["AIMockLaunchError"],
    ).AIMockLaunchError("node missing")
    env = StubAIMockEnvironment()

    with patch(
        "tests.system_verify.modes.stub_aimock.AIMockSidecar",
        return_value=sidecar,
    ), pytest.raises(SystemExit):
        prepare_mode(
            env,
            spec,
            profile,
            config=config,
            fail_on_launch_error=True,
            print_env_notice=False,
        )


def test_stub_backend_spawn_error_exits_when_configured() -> None:
    spec, config, profile = _stub_spec()
    session = MagicMock()
    sidecar = MagicMock()
    sidecar.__enter__.return_value = session
    env = StubAIMockEnvironment()

    with patch(
        "tests.system_verify.modes.stub_aimock.AIMockSidecar",
        return_value=sidecar,
    ), patch(
        "tests.system_verify.modes.stub_aimock.inject_stub_backend_env",
    ), patch(
        "tests.system_verify.modes.stub_aimock.spawn_backend_proc",
        side_effect=RuntimeError("port in use"),
    ), pytest.raises(SystemExit):
        prepare_mode(
            env,
            spec,
            profile,
            config=config,
            spawn_backend=True,
            fail_on_backend_spawn_error=True,
            print_env_notice=False,
        )
    sidecar.__exit__.assert_called_once()


def test_stub_backend_ready_error_exits_when_configured() -> None:
    spec, config, profile = _stub_spec()
    session = MagicMock()
    sidecar = MagicMock()
    sidecar.__enter__.return_value = session
    env = StubAIMockEnvironment()

    with patch(
        "tests.system_verify.modes.stub_aimock.AIMockSidecar",
        return_value=sidecar,
    ), patch(
        "tests.system_verify.modes.stub_aimock.inject_stub_backend_env",
    ), patch(
        "tests.system_verify.modes.stub_aimock.assert_backend_stub_llm_ready",
        side_effect=RuntimeError("backend not wired"),
    ), pytest.raises(SystemExit):
        prepare_mode(
            env,
            spec,
            profile,
            config=config,
            assert_backend_ready=True,
            fail_on_backend_ready_error=True,
            print_env_notice=False,
        )
    sidecar.__exit__.assert_called_once()
