"""Mode environment protocol and shared helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from tests.system_verify.core.config import VerifyConfig
from tests.system_verify.core.run_spec import RunSpec
from tests.system_verify.profiles.registry import VerificationProfile


@dataclass
class ModeHandle:
    """Runtime state for a prepared mode environment."""

    config: VerifyConfig
    spec: RunSpec
    profile: VerificationProfile
    manifest_info: dict[str, Any] = field(default_factory=dict)
    aimock_session: Any | None = None
    _sidecar: Any | None = field(default=None, repr=False)
    _backend_proc: Any | None = field(default=None, repr=False)


@runtime_checkable
class ModeEnvironment(Protocol):
    async def prepare(
        self,
        spec: RunSpec,
        profile: VerificationProfile,
        *,
        config: VerifyConfig,
        **options: Any,
    ) -> ModeHandle: ...

    async def validate_prerequisites(self, handle: ModeHandle) -> list[str]: ...

    async def cleanup(self, handle: ModeHandle) -> None: ...


def resolve_mode_environment(spec: RunSpec) -> ModeEnvironment:
    """Return the mode environment implementation for a run spec."""
    if spec.llm_mode == "real":
        from tests.system_verify.modes.real_llm import RealLLMEnvironment

        return RealLLMEnvironment()
    from tests.system_verify.modes.stub_aimock import StubAIMockEnvironment

    return StubAIMockEnvironment()


def prepare_mode(
    env: ModeEnvironment,
    spec: RunSpec,
    profile: VerificationProfile,
    *,
    config: VerifyConfig,
    **options: Any,
) -> ModeHandle:
    """Synchronous prepare for pytest/CLI until orchestrator owns the async loop."""
    prepare_sync = getattr(env, "prepare_sync", None)
    if prepare_sync is not None:
        return prepare_sync(spec, profile, config=config, **options)
    return asyncio.run(env.prepare(spec, profile, config=config, **options))


def validate_mode_prerequisites(env: ModeEnvironment, handle: ModeHandle) -> list[str]:
    """Synchronous prerequisite validation for pytest/CLI."""
    validate_sync = getattr(env, "validate_prerequisites_sync", None)
    if validate_sync is not None:
        return validate_sync(handle)
    return asyncio.run(env.validate_prerequisites(handle))


def cleanup_mode(env: ModeEnvironment, handle: ModeHandle) -> None:
    """Synchronous cleanup for pytest/CLI."""
    cleanup_sync = getattr(env, "cleanup_sync", None)
    if cleanup_sync is not None:
        cleanup_sync(handle)
        return
    asyncio.run(env.cleanup(handle))
