"""Pytest entry points for system verification scenarios."""

from __future__ import annotations

import pytest

from .config import apply_param_set
from .scenarios.s0_connectivity import run_s0
from .scenarios.s1_import import run_s1
from .scenarios.s2_continuous_reading import run_s2
from .scenarios.s3_fast_scroll import run_s3
from .scenarios.s4_long_context import run_s4
from .suite import run_mvp_suite, run_real_happy_path_suite


def _with_param_set(verify_config, name: str):
    apply_param_set(verify_config, name)
    return verify_config


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
    await run_s1(run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx)


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s2_continuous_reading(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s2(run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx)


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s3_fast_scroll(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s3(run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx)


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_s4_long_context(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    await run_s4(run_manager, verify_config, metrics, corpus_manager, suite_ctx=suite_ctx)


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
@pytest.mark.system
@pytest.mark.asyncio
async def test_r1_happy_path_a2_stub(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    cfg = _with_param_set(verify_config, "r1_a2_stub")
    await run_real_happy_path_suite(
        run_manager,
        cfg,
        metrics,
        "tests/corpus/manifest.toml",
        suite_ctx=suite_ctx,
        coverage="A2",
    )


@pytest.mark.system_verify
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r1_happy_path_a2_real(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    cfg = _with_param_set(verify_config, "r1_a2_real")
    if not cfg.is_real_llm:
        pytest.skip("Requires --llm-mode real matching r1_a2_real param set")
    await run_real_happy_path_suite(
        run_manager,
        cfg,
        metrics,
        "tests/corpus/manifest.toml",
        suite_ctx=suite_ctx,
        coverage="A2",
    )


@pytest.mark.system_verify
@pytest.mark.system
@pytest.mark.asyncio
async def test_r1_happy_path_a3_stub(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    cfg = _with_param_set(verify_config, "r1_a3_stub")
    await run_real_happy_path_suite(
        run_manager,
        cfg,
        metrics,
        "tests/corpus/manifest.toml",
        suite_ctx=suite_ctx,
        coverage="A3",
    )


@pytest.mark.system_verify
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r1_happy_path_a3_real(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    cfg = _with_param_set(verify_config, "r1_a3_real")
    if not cfg.is_real_llm:
        pytest.skip("Requires --llm-mode real matching r1_a3_real param set")
    await run_real_happy_path_suite(
        run_manager,
        cfg,
        metrics,
        "tests/corpus/manifest.toml",
        suite_ctx=suite_ctx,
        coverage="A3",
    )


# Backward-compatible aliases for older pytest invocations.
@pytest.mark.system_verify
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r1_real_happy_path_a2_comments(
    run_manager, verify_config, metrics, corpus_manager, suite_ctx
):
    cfg = _with_param_set(verify_config, "r1_a2_real")
    if not cfg.is_real_llm:
        pytest.skip("Requires --llm-mode real matching r1_a2_real param set")
    await run_real_happy_path_suite(
        run_manager,
        cfg,
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
    cfg = _with_param_set(verify_config, "r1_a3_real")
    if not cfg.is_real_llm:
        pytest.skip("Requires --llm-mode real matching r1_a3_real param set")
    await run_real_happy_path_suite(
        run_manager,
        cfg,
        metrics,
        "tests/corpus/manifest.toml",
        suite_ctx=suite_ctx,
        coverage="A3",
    )
