"""Pure budget and verify-metrics guardrail assertions (no HTTP)."""

from __future__ import annotations

from typing import Any

from ..core.config import VerifyConfig
from ..core.run_manager import RealLLMCallTracker
from ..core.scenario import StepAssertionError, assert_that


def assert_real_llm_budget_within_limits(
    tracker: RealLLMCallTracker,
    config: VerifyConfig,
) -> None:
    """Enforce configured real-LLM budget guardrails on the run tracker."""
    if not config.params.budget.enforce:
        return

    tracker.check_budget(config)
    if not tracker.budget_exceeded:
        return

    raise StepAssertionError(
        assertion=tracker.budget_reason or "real_llm_budget_exceeded",
        message="Real LLM budget guardrail exceeded",
        actual={
            "call_count": tracker.call_count,
            "input_tokens": tracker.input_tokens,
            "output_tokens": tracker.output_tokens,
            "max_input_tokens_single": tracker.max_input_tokens_single,
            "max_output_tokens_single": tracker.max_output_tokens_single,
            "total_cost_usd": tracker.total_cost_usd,
        },
    )


def assert_a3_compaction_phase_coverage(
    tracker: RealLLMCallTracker,
    compaction_runs: list[dict[str, Any]],
) -> None:
    """Assert A3 compaction agent runs and phase coverage were recorded."""
    assert_that.gte(
        len(compaction_runs),
        1,
        label="real_compaction_agent_runs",
    )
    assert_that.is_true(
        tracker.phase_coverage.get("A3_compaction", False),
        "A3_compaction phase coverage must be marked true",
    )


def assert_a4_full_flow_phase_coverage(
    tracker: RealLLMCallTracker,
    chat_runs: list[dict[str, Any]],
) -> None:
    """Assert A4 post-compaction chat and phase coverage were recorded."""
    assert_that.gte(len(chat_runs), 1, label="real_chat_agent_runs")
    assert_that.is_true(
        tracker.phase_coverage.get("A4_full_flow", False),
        "A4_full_flow phase coverage must be marked true",
    )

