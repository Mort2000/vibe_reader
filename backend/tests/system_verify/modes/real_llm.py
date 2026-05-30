"""Real LLM mode environment."""

from __future__ import annotations

from typing import Any

from tests.system_verify.core.config import VerifyConfig, validate_real_llm_config
from tests.system_verify.core.run_spec import RunSpec
from tests.system_verify.modes.base import ModeHandle
from tests.system_verify.profiles.registry import VerificationProfile


class RealLLMEnvironment:
    """Validate real LLM configuration; never start AIMock."""

    def prepare_sync(
        self,
        spec: RunSpec,
        profile: VerificationProfile,
        *,
        config: VerifyConfig,
        **options: Any,
    ) -> ModeHandle:
        return ModeHandle(
            config=config,
            spec=spec,
            profile=profile,
            manifest_info={"real_llm": True},
        )

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
        return validate_real_llm_config(handle.config)

    async def validate_prerequisites(self, handle: ModeHandle) -> list[str]:
        return self.validate_prerequisites_sync(handle)

    def cleanup_sync(self, handle: ModeHandle) -> None:
        return None

    async def cleanup(self, handle: ModeHandle) -> None:
        return None
