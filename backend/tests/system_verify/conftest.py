"""Pytest fixtures and markers for system verification tests."""
from __future__ import annotations

import asyncio
import os
import pathlib

import pytest

from .config import VerifyConfig, load_verify_config
from .corpus import CorpusManager
from .metrics_collector import MetricsAggregator
from .run import RunManager


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "system_llm: system verification tests requiring real backend and LLM")
    config.addinivalue_line("markers", "system_verify: all system verification tests")


@pytest.fixture(scope="session")
def verify_config() -> VerifyConfig:
    return load_verify_config()


@pytest.fixture(scope="session")
def run_manager(verify_config: VerifyConfig) -> RunManager:
    run_id = os.environ.get("VIBE_READER_VERIFY_RUN_ID") or None
    mgr = RunManager(verify_config, run_id=run_id)
    mgr.start()
    yield mgr
    mgr.finish()
    mgr.write_manifest()


@pytest.fixture(scope="session")
def metrics(run_manager: RunManager) -> MetricsAggregator:
    return MetricsAggregator(run_manager)


@pytest.fixture(scope="session")
def corpus_manager(verify_config: VerifyConfig) -> CorpusManager:
    manifest_path = pathlib.Path("tests/corpus/manifest.toml")
    cm = CorpusManager(verify_config, manifest_path)
    cm.load()
    return cm


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
