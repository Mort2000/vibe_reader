"""Backward-compatible pytest aliases for pre-split R1 scenario names."""

from __future__ import annotations

import pytest

from .core.orchestrator import run_scenario
from .scenarios.registry import SCENARIOS

pytestmark = pytest.mark.usefixtures("require_integration_ready")


@pytest.mark.system_verify
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r1_real_happy_path_a2_comments(verify_session_r1_a2_real):
    """Alias for test_r1_happy_path_a2_real (pre Phase 6 split naming)."""
    if not verify_session_r1_a2_real.config.is_real_llm:
        pytest.skip("Requires --llm-mode real matching r1_a2_real param set")
    await run_scenario(verify_session_r1_a2_real, SCENARIOS["R1_A2_comments"])


@pytest.mark.system_verify
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r1_real_happy_path_a3_compaction(verify_session_r1_a3_real):
    """Alias for test_r1_happy_path_a3_real (pre Phase 6 split naming)."""
    if not verify_session_r1_a3_real.config.is_real_llm:
        pytest.skip("Requires --llm-mode real matching r1_a3_real param set")
    await run_scenario(verify_session_r1_a3_real, SCENARIOS["R1_A3_compaction"])
