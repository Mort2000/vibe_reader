"""Verification suite orchestration shared by CLI and pytest."""

from __future__ import annotations

from typing import Any

from .config import VerifyConfig
from .corpus import CorpusManager
from .metrics_collector import MetricsAggregator
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
    """Run S0–S3 sequentially, optionally sharing context between scenarios."""
    from .scenarios.s0_connectivity import run_s0
    from .scenarios.s1_import import run_s1
    from .scenarios.s2_continuous_reading import run_s2
    from .scenarios.s3_fast_scroll import run_s3

    ctx = suite_ctx if suite_ctx is not None else {}
    corpus = prepare_corpus(mgr, config, corpus_path)
    await run_s0(mgr, config, metrics)
    await run_s1(mgr, config, metrics, corpus, suite_ctx=ctx)
    await run_s2(mgr, config, metrics, corpus, suite_ctx=ctx)
    await run_s3(mgr, config, metrics, corpus, suite_ctx=ctx)
