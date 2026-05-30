"""Pytest entry points for system verification scenarios."""

from __future__ import annotations

import pytest

from .core.orchestrator import run_scenario, run_suite_scenarios
from .scenarios.registry import SCENARIOS

# All tests in this module require a live backend; skip when prerequisites fail.
pytestmark = pytest.mark.usefixtures(
    "require_integration_ready",
    "reset_verify_data_before_scenario",
)


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s0_connectivity(verify_session):
    await run_scenario(verify_session, SCENARIOS["S0_connectivity"])


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s1_book_import(verify_session):
    await run_scenario(verify_session, SCENARIOS["S1_book_import"])


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s2_continuous_reading(verify_session):
    await run_scenario(verify_session, SCENARIOS["S2_continuous_reading"])


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s3_fast_scroll(verify_session):
    await run_scenario(verify_session, SCENARIOS["S3_fast_scroll"])


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s4_long_context(verify_session):
    await run_scenario(verify_session, SCENARIOS["S4_long_context"])


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_mvp_suite(verify_session):
    await run_suite_scenarios(
        verify_session.run_manager,
        verify_session.config,
        verify_session.metrics,
        verify_session.corpus_path,
        suite=verify_session.spec.suite,
        suite_ctx=verify_session.suite_ctx,
        spec=verify_session.spec,
    )


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_r1_happy_path_a2_stub(verify_session_r1_a2_stub):
    await run_scenario(verify_session_r1_a2_stub, SCENARIOS["R1_A2_comments"])


@pytest.mark.system_verify
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r1_happy_path_a2_real(verify_session_r1_a2_real):
    if not verify_session_r1_a2_real.config.is_real_llm:
        pytest.skip("Requires --llm-mode real matching r1_a2_real param set")
    await run_scenario(verify_session_r1_a2_real, SCENARIOS["R1_A2_comments"])


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_r1_happy_path_a3_stub(verify_session_r1_a3_stub):
    await run_scenario(verify_session_r1_a3_stub, SCENARIOS["R1_A3_compaction"])


@pytest.mark.system_verify
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r1_happy_path_a3_real(verify_session_r1_a3_real):
    if not verify_session_r1_a3_real.config.is_real_llm:
        pytest.skip("Requires --llm-mode real matching r1_a3_real param set")
    await run_scenario(verify_session_r1_a3_real, SCENARIOS["R1_A3_compaction"])
