"""Pytest entry points for system verification scenarios."""

from __future__ import annotations

import pytest

from .scenarios.s0_connectivity import run_s0
from .scenarios.s1_import import run_s1
from .scenarios.s2_continuous_reading import run_s2
from .scenarios.s3_fast_scroll import run_s3
from .scenarios.s4_long_context import run_s4
from .suite import run_mvp_suite, run_real_happy_path_suite


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s0_connectivity(run_manager, verify_config, metrics):
    await run_s0(run_manager, verify_config, metrics)


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s1_book_import(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s1(
        run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx
    )


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s2_continuous_reading(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s2(
        run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx
    )


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s3_fast_scroll(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s3(
        run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx
    )


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s4_long_context(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s4(
        run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx
    )


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_mvp_suite(run_manager, verify_config, metrics, suite_ctx):
    corpus_path = "tests/corpus/manifest.toml"
    await run_mvp_suite(
        run_manager,
        verify_config,
        metrics,
        corpus_path,
        suite_ctx=suite_ctx,
    )


@pytest.mark.system_verify
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r1_real_happy_path_a2_comments(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    if not verify_config.is_real_llm:
        pytest.skip("Requires --llm-mode real")
    await run_real_happy_path_suite(
        run_manager,
        verify_config,
        metrics,
        "tests/corpus/manifest.toml",
        suite_ctx=suite_ctx,
        coverage="A2",
    )


@pytest.mark.system_verify
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r1_real_happy_path_a3_compaction(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    if not verify_config.is_real_llm:
        pytest.skip("Requires --llm-mode real")
    await run_real_happy_path_suite(
        run_manager,
        verify_config,
        metrics,
        "tests/corpus/manifest.toml",
        suite_ctx=suite_ctx,
        coverage="A3",
    )
