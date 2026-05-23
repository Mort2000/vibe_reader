"""Verification framework configuration.

Loads from a TOML config file + environment variable overrides.
Does not read or write the user's daily ~/.vibe_reader/ directory.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

import toml


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


@dataclass
class TargetConfig:
    base_url: str = "http://127.0.0.1:8000"
    data_dir: str = "/tmp/vibe_reader_verify"


@dataclass
class VerifyLLMConfig:
    base_url: str = ""
    api_key_env: str = "VIBE_READER_LLM_API_KEY"
    model: str = "deepseek-v4-flash"
    temperature: float = 0.4
    timeout_s: int = 120

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")


@dataclass
class RunConfig:
    suite: str = "mvp"
    max_wait_comment_window_s: int = 180
    max_wait_compaction_s: int = 240
    max_wait_chat_s: int = 120
    progress_step_delay_ms: int = 300
    seed: int = 20260522


@dataclass
class MetricsConfig:
    collect_otel: bool = True
    collect_logfire: bool = True
    collect_sse_events: bool = True
    collect_provider_usage: bool = True


@dataclass
class AuditConfig:
    sample_comments_per_window: int = 3
    sample_chat_turns_per_probe: int = 2
    include_prompt_manifest: bool = True
    include_full_prompt: bool = False
    include_original_excerpts: bool = True


@dataclass
class VerifyConfig:
    target: TargetConfig = field(default_factory=TargetConfig)
    llm: VerifyLLMConfig = field(default_factory=VerifyLLMConfig)
    run: RunConfig = field(default_factory=RunConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)

    @property
    def target_data_dir(self) -> pathlib.Path:
        return pathlib.Path(self.target.data_dir)


def load_verify_config(path: str | pathlib.Path | None = None) -> VerifyConfig:
    """Load verification config from TOML file with env var overrides."""
    config_path = pathlib.Path(path) if path else None

    if config_path is None:
        env_path = _env("VIBE_READER_VERIFY_CONFIG")
        if env_path:
            config_path = pathlib.Path(env_path)
        else:
            default = pathlib.Path("tests/corpus/verify.toml")
            if default.exists():
                config_path = default

    raw: dict = {}
    if config_path and config_path.exists():
        raw = toml.load(str(config_path))

    target_raw = raw.get("target", {})
    target = TargetConfig(
        base_url=_env("VIBE_READER_VERIFY_TARGET_URL")
        or target_raw.get("base_url", "http://127.0.0.1:8000"),
        data_dir=_env("VIBE_READER_VERIFY_DATA_DIR")
        or target_raw.get("data_dir", "/tmp/vibe_reader_verify"),
    )

    llm_raw = raw.get("llm", {})
    llm = VerifyLLMConfig(
        base_url=_env("VIBE_READER_LLM_BASE_URL") or llm_raw.get("base_url", ""),
        api_key_env=llm_raw.get("api_key_env", "VIBE_READER_LLM_API_KEY"),
        model=_env("VIBE_READER_LLM_MODEL")
        or llm_raw.get("model", "deepseek-v4-flash"),
        temperature=llm_raw.get("temperature", 0.4),
        timeout_s=llm_raw.get("timeout_s", 120),
    )

    run_raw = raw.get("run", {})
    run = RunConfig(
        suite=_env("VIBE_READER_VERIFY_SUITE") or run_raw.get("suite", "mvp"),
        max_wait_comment_window_s=run_raw.get("max_wait_comment_window_s", 180),
        max_wait_compaction_s=run_raw.get("max_wait_compaction_s", 240),
        max_wait_chat_s=run_raw.get("max_wait_chat_s", 120),
        progress_step_delay_ms=run_raw.get("progress_step_delay_ms", 300),
        seed=run_raw.get("seed", 20260522),
    )

    metrics_raw = raw.get("metrics", {})
    metrics = MetricsConfig(
        collect_otel=metrics_raw.get("collect_otel", True),
        collect_logfire=metrics_raw.get("collect_logfire", True),
        collect_sse_events=metrics_raw.get("collect_sse_events", True),
        collect_provider_usage=metrics_raw.get("collect_provider_usage", True),
    )

    audit_raw = raw.get("audit", {})
    audit = AuditConfig(
        sample_comments_per_window=audit_raw.get("sample_comments_per_window", 3),
        sample_chat_turns_per_probe=audit_raw.get("sample_chat_turns_per_probe", 2),
        include_prompt_manifest=audit_raw.get("include_prompt_manifest", True),
        include_full_prompt=audit_raw.get("include_full_prompt", False),
        include_original_excerpts=audit_raw.get("include_original_excerpts", True),
    )

    return VerifyConfig(
        target=target,
        llm=llm,
        run=run,
        metrics=metrics,
        audit=audit,
    )
