"""Immutable policy objects derived from param sets."""

from __future__ import annotations

from dataclasses import dataclass

from tests.system_verify.core.config import (
    AssertionParams,
    BudgetParams,
    LongFlowParams,
    ParamSet,
)


@dataclass(frozen=True)
class PacingPolicy:
    progress_step_delay_ms: int
    read_batch_size: int
    compaction_advance_batch_size: int
    max_wait_comment_window_s: int
    max_wait_compaction_s: int
    max_wait_chat_s: int


@dataclass(frozen=True)
class LongFlowPolicy:
    require_compaction: bool
    test_compaction_trigger_tokens: int
    test_compaction_min_source_tokens: int
    test_compaction_min_source_paragraphs: int
    min_comment_windows: int
    min_chat_turns: int
    reading_stop_mode: str
    post_compaction_comment_windows: int


@dataclass(frozen=True)
class BudgetPolicy:
    max_calls: int
    max_input_tokens_per_call: int
    max_output_tokens_per_call: int
    max_total_cost_usd: float
    enforce: bool
    track_usage: bool


@dataclass(frozen=True)
class AssertionPolicy:
    strict_done_without_comments: bool
    require_compaction_audit_real: bool
    allow_probe_without_real_llm_flag: bool


@dataclass(frozen=True)
class AuditPolicy:
    require_agent_artifacts: bool


def pacing_policy_from_param_set(params: ParamSet) -> PacingPolicy:
    return PacingPolicy(
        progress_step_delay_ms=params.progress_step_delay_ms,
        read_batch_size=params.read_batch_size,
        compaction_advance_batch_size=params.compaction_advance_batch_size,
        max_wait_comment_window_s=params.max_wait_comment_window_s,
        max_wait_compaction_s=params.max_wait_compaction_s,
        max_wait_chat_s=params.max_wait_chat_s,
    )


def long_flow_policy_from_params(long_flow: LongFlowParams) -> LongFlowPolicy:
    return LongFlowPolicy(
        require_compaction=long_flow.require_compaction,
        test_compaction_trigger_tokens=long_flow.test_compaction_trigger_tokens,
        test_compaction_min_source_tokens=long_flow.test_compaction_min_source_tokens,
        test_compaction_min_source_paragraphs=long_flow.test_compaction_min_source_paragraphs,
        min_comment_windows=long_flow.min_comment_windows,
        min_chat_turns=long_flow.min_chat_turns,
        reading_stop_mode=long_flow.reading_stop_mode,
        post_compaction_comment_windows=long_flow.post_compaction_comment_windows,
    )


def budget_policy_from_params(budget: BudgetParams) -> BudgetPolicy:
    return BudgetPolicy(
        max_calls=budget.max_calls,
        max_input_tokens_per_call=budget.max_input_tokens_per_call,
        max_output_tokens_per_call=budget.max_output_tokens_per_call,
        max_total_cost_usd=budget.max_total_cost_usd,
        enforce=budget.enforce,
        track_usage=budget.track_usage,
    )


def assertion_policy_from_params(assertions: AssertionParams) -> AssertionPolicy:
    return AssertionPolicy(
        strict_done_without_comments=assertions.strict_done_without_comments,
        require_compaction_audit_real=assertions.require_compaction_audit_real,
        allow_probe_without_real_llm_flag=assertions.allow_probe_without_real_llm_flag,
    )


def audit_policy_from_param_set(params: ParamSet) -> AuditPolicy:
    return AuditPolicy(
        require_agent_artifacts=params.llm_mode == "real"
        or params.assertions.require_compaction_audit_real,
    )
