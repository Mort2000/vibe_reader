"""Profile registry: param set loading and VerificationProfile resolution."""

from __future__ import annotations

from dataclasses import dataclass

from tests.system_verify.core.config_loader import resolve_param_set_name
from tests.system_verify.core.config import ParamSet, VerifyConfig
from tests.system_verify.profiles.policies import (
    AssertionPolicy,
    AuditPolicy,
    BudgetPolicy,
    LongFlowPolicy,
    PacingPolicy,
    assertion_policy_from_params,
    audit_policy_from_param_set,
    budget_policy_from_params,
    long_flow_policy_from_params,
    pacing_policy_from_param_set,
)

# Param set file name -> canonical profile name (plan §5.2).
PARAM_SET_TO_PROFILE: dict[str, str] = {
    "mvp": "mvp_stub",
    "r1_a2_stub": "r1_a2_stub",
    "r1_a2_real": "r1_a2_real",
    "r1_a3_stub": "r1_a3_stub",
    "r1_a3_real": "r1_a3_real",
}

PROFILE_TO_PARAM_SET: dict[str, str] = {v: k for k, v in PARAM_SET_TO_PROFILE.items()}


@dataclass(frozen=True)
class VerificationProfile:
    name: str
    llm_mode: str
    aimock_profile: str | None
    pacing: PacingPolicy
    long_flow: LongFlowPolicy
    budget_policy: BudgetPolicy
    assertion_policy: AssertionPolicy
    audit_policy: AuditPolicy
    required_probes: tuple[str, ...]
    param_set_name: str


def profile_name_for_param_set(param_set_name: str) -> str:
    return PARAM_SET_TO_PROFILE.get(param_set_name, param_set_name)


def param_set_name_for_profile(profile_name: str) -> str:
    return PROFILE_TO_PARAM_SET.get(profile_name, profile_name)


def required_probes_for_profile(profile_name: str) -> tuple[str, ...]:
    if profile_name.startswith("r1_"):
        return ("happy_path_current",)
    return ()


def profile_from_param_set(params: ParamSet) -> VerificationProfile:
    profile_name = profile_name_for_param_set(params.name)
    return VerificationProfile(
        name=profile_name,
        llm_mode=params.llm_mode,
        aimock_profile=params.aimock_profile,
        pacing=pacing_policy_from_param_set(params),
        long_flow=long_flow_policy_from_params(params.long_flow),
        budget_policy=budget_policy_from_params(params.budget),
        assertion_policy=assertion_policy_from_params(params.assertions),
        audit_policy=audit_policy_from_param_set(params),
        required_probes=required_probes_for_profile(profile_name),
        param_set_name=params.name,
    )


def get_profile(name: str, config: VerifyConfig) -> VerificationProfile:
    """Return a profile by profile name or param set name."""
    param_set_name = param_set_name_for_profile(name)
    if param_set_name not in config.param_sets:
        if name in config.param_sets:
            param_set_name = name
        else:
            known = ", ".join(sorted(config.param_sets)) or "(none)"
            raise KeyError(f"Unknown profile {name!r}; known param sets: {known}")
    return profile_from_param_set(config.param_sets[param_set_name])


def resolve_profile(
    config: VerifyConfig,
    *,
    suite: str | None = None,
    coverage: str | None = None,
    llm_mode: str | None = None,
    explicit_param_set: str | None = None,
) -> VerificationProfile:
    """Resolve the active profile from suite/coverage/llm_mode hints."""
    param_set_name = resolve_param_set_name(
        config,
        explicit=explicit_param_set,
        suite=suite,
        coverage=coverage or "A2",
        llm_mode_hint=llm_mode or config.llm.mode,
    )
    return profile_from_param_set(config.param_sets[param_set_name])
