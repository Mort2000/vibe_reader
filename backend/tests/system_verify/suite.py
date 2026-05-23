"""Verification suite orchestration shared by CLI and pytest."""

from __future__ import annotations

from typing import Any

from .config import VerifyConfig, validate_real_llm_config
from .corpus import CorpusManager
from .metrics_collector import MetricsAggregator
from .report_generator import generate_reports
from .run import RunManager


def prepare_corpus(mgr: RunManager, config: VerifyConfig, corpus_path: str) -> CorpusManager:
    corpus = CorpusManager(config, corpus_path)
    corpus.load()
    if not corpus.validate():
        errors = "\n".join(corpus.validation_errors)
        raise RuntimeError(f"Corpus validation failed:\n{errors}")

    resolved = corpus.resolve(mgr)
    print(f"Corpus resolved: {resolved}")
    return corpus


async def run_mvp_suite(
    mgr: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus_path: str,
    *,
    suite_ctx: dict[str, Any] | None = None,
) -> None:
    """Run S0–S3 sequentially using default stub LLM mode."""
    from .scenarios.s0_connectivity import run_s0
    from .scenarios.s1_import import run_s1
    from .scenarios.s2_continuous_reading import run_s2
    from .scenarios.s3_fast_scroll import run_s3

    if config.is_real_llm:
        raise RuntimeError("mvp/smoke suites must run with llm.mode=stub")

    ctx = suite_ctx if suite_ctx is not None else {}
    corpus = prepare_corpus(mgr, config, corpus_path)
    await run_s0(mgr, config, metrics)
    await run_s1(mgr, config, metrics, corpus, suite_ctx=ctx)
    await run_s2(mgr, config, metrics, corpus, suite_ctx=ctx)
    await run_s3(mgr, config, metrics, corpus, suite_ctx=ctx)


async def run_real_happy_path_suite(
    mgr: RunManager,
    config: VerifyConfig,
    metrics: MetricsAggregator,
    corpus_path: str,
    *,
    suite_ctx: dict[str, Any] | None = None,
    coverage: str = "A2",
) -> None:
    """Run R1 real LLM happy path for the requested coverage phase."""
    if not config.is_real_llm:
        raise RuntimeError("real-happy-path requires llm.mode=real")

    errors = validate_real_llm_config(config)
    if errors:
        raise RuntimeError("Real LLM config invalid: " + "; ".join(errors))

    ctx = suite_ctx if suite_ctx is not None else {}
    corpus = prepare_corpus(mgr, config, corpus_path)

    if coverage.upper() == "A2":
        from .scenarios.r1_real_happy_path import run_r1_a2_comments

        await run_r1_a2_comments(mgr, config, metrics, corpus, suite_ctx=ctx)
        return

    raise RuntimeError(f"real-happy-path coverage '{coverage}' is not implemented yet")


def finalize_reports(mgr: RunManager) -> dict[str, Any]:
    paths = generate_reports(mgr.base_dir)
    print(f"Report written: {paths['summary']}")
    return {key: str(path) for key, path in paths.items()}
