from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TriggerDecision:
    preflight_triggered: bool
    hard_triggered: bool


def evaluate_triggers(
    estimated_tokens: int,
    live_original_tokens: int,
    completed_chunks: int,
    *,
    preflight_trigger_tokens: int,
    max_live_original_tokens: int,
    max_completed_before_compaction: int,
    min_completed_before_compaction: int,
    compression_trigger_tokens: int,
) -> TriggerDecision:
    volume_pressure = (
        estimated_tokens > preflight_trigger_tokens
        or live_original_tokens > max_live_original_tokens
        or completed_chunks >= max_completed_before_compaction
    )
    preflight = volume_pressure and completed_chunks >= min_completed_before_compaction
    hard = estimated_tokens > compression_trigger_tokens
    return TriggerDecision(preflight_triggered=preflight, hard_triggered=hard)


def exceeds_compression_threshold(
    estimated_tokens: int,
    compression_trigger_tokens: int,
) -> bool:
    return estimated_tokens > compression_trigger_tokens


def can_use_emergency_overflow(
    estimated_tokens: int,
    overflow_already_used: bool,
    *,
    allow_emergency_overflow: bool,
    emergency_cap_tokens: int,
    compression_trigger_tokens: int,
) -> bool:
    if not exceeds_compression_threshold(
        estimated_tokens, compression_trigger_tokens
    ):
        return False
    return (
        allow_emergency_overflow
        and not overflow_already_used
        and estimated_tokens <= emergency_cap_tokens
    )
