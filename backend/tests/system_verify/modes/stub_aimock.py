"""Stub AIMock sidecar mode environment."""

from __future__ import annotations

import sys
from typing import Any

from tests.system_verify.core.config import VerifyConfig
from tests.system_verify.core.run_spec import RunSpec
from tests.system_verify.llm_stub.aimock_launcher import AIMockLaunchError, AIMockSidecar
from tests.system_verify.llm_stub.env import (
    BackendProcess,
    assert_backend_stub_llm_ready,
    inject_stub_backend_env,
    print_stub_backend_env_notice,
    spawn_backend as spawn_backend_proc,
    validate_backend_stub_llm,
)
from tests.system_verify.modes.base import ModeHandle
from tests.system_verify.profiles.registry import VerificationProfile


def aimock_manifest_info(session: Any) -> dict[str, str | bool]:
    """Build manifest fields for RunManager.set_aimock_info."""
    return {
        "provider": "aimock",
        "version": session.version,
        "base_url": session.base_url,
        "fixture_hash": session.fixture_hash,
        "profile_hash": session.profile_hash,
        "strict": session.strict,
        "profile": session.profile,
    }


class StubAIMockEnvironment:
    """Start AIMock, inject stub backend env, and optionally spawn backend."""

    def prepare_sync(
        self,
        spec: RunSpec,
        profile: VerificationProfile,
        *,
        config: VerifyConfig,
        spawn_backend: bool | None = None,
        dry_run: bool = False,
        assert_backend_ready: bool = False,
        print_env_notice: bool = True,
        fail_on_launch_error: bool = False,
        fail_on_backend_spawn_error: bool = False,
        fail_on_backend_ready_error: bool = False,
    ) -> ModeHandle:
        handle = ModeHandle(config=config, spec=spec, profile=profile)
        if config.is_real_llm or not config.llm_stub.aimock.enabled:
            return handle

        should_spawn = spec.spawn_backend if spawn_backend is None else spawn_backend
        sidecar = AIMockSidecar(config)
        try:
            session = sidecar.__enter__()
        except AIMockLaunchError as exc:
            if fail_on_launch_error:
                print(f"AIMock startup failed: {exc}", file=sys.stderr)
                sys.exit(1)
            raise

        inject_stub_backend_env(session, config)
        if print_env_notice:
            print_stub_backend_env_notice(session, config)

        backend_proc: BackendProcess | None = None
        if should_spawn:
            try:
                backend_proc = spawn_backend_proc(config, session)
            except RuntimeError as exc:
                sidecar.__exit__(None, None, None)
                if fail_on_backend_spawn_error:
                    print(f"Backend spawn failed: {exc}", file=sys.stderr)
                    sys.exit(1)
                raise
            if fail_on_backend_spawn_error:
                print(f"Backend spawned at {config.target.base_url}")
        elif assert_backend_ready and not dry_run:
            try:
                assert_backend_stub_llm_ready(config, session)
            except RuntimeError as exc:
                sidecar.__exit__(None, None, None)
                if fail_on_backend_ready_error:
                    print(f"Backend stub LLM check failed: {exc}", file=sys.stderr)
                    sys.exit(1)
                raise

        handle.aimock_session = session
        handle.manifest_info = aimock_manifest_info(session)
        handle._sidecar = sidecar
        handle._backend_proc = backend_proc
        return handle

    async def prepare(
        self,
        spec: RunSpec,
        profile: VerificationProfile,
        *,
        config: VerifyConfig,
        **options: Any,
    ) -> ModeHandle:
        return self.prepare_sync(spec, profile, config=config, **options)

    def validate_prerequisites_sync(self, handle: ModeHandle) -> list[str]:
        if handle.config.is_real_llm:
            return []
        if handle.aimock_session is None or not handle.config.llm_stub.aimock.enabled:
            return []
        llm_errors = validate_backend_stub_llm(handle.config, handle.aimock_session)
        if not llm_errors:
            return []
        return [
            "stub LLM not wired on backend: "
            + "; ".join(llm_errors)
            + " (restart backend with AIMock env or use --spawn-backend)"
        ]

    async def validate_prerequisites(self, handle: ModeHandle) -> list[str]:
        return self.validate_prerequisites_sync(handle)

    def cleanup_sync(self, handle: ModeHandle) -> None:
        backend_proc = handle._backend_proc
        if backend_proc is not None:
            backend_proc.stop()
            handle._backend_proc = None
        sidecar = handle._sidecar
        if sidecar is not None:
            sidecar.__exit__(None, None, None)
            handle._sidecar = None
            handle.aimock_session = None

    async def cleanup(self, handle: ModeHandle) -> None:
        self.cleanup_sync(handle)
