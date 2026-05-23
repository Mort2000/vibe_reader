"""Pytest entry points for system verification scenarios."""

from __future__ import annotations

import pytest

from .scenarios.s0_connectivity import run_s0
from .scenarios.s1_import import run_s1
from .scenarios.s2_continuous_reading import run_s2
from .scenarios.s3_fast_scroll import run_s3
from .suite import run_mvp_suite


@pytest.mark.system_verify
@pytest.mark.system_llm
@pytest.mark.asyncio
async def test_s0_connectivity(run_manager, verify_config, metrics):
    await run_s0(run_manager, verify_config, metrics)


@pytest.mark.system_verify
@pytest.mark.system_llm
@pytest.mark.asyncio
async def test_s1_book_import(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s1(run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx)


@pytest.mark.system_verify
@pytest.mark.system_llm
@pytest.mark.asyncio
async def test_s2_continuous_reading(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s2(run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx)


@pytest.mark.system_verify
@pytest.mark.system_llm
@pytest.mark.asyncio
async def test_s3_fast_scroll(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s3(run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx)


@pytest.mark.system_verify
@pytest.mark.system_llm
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
