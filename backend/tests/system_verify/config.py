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
class LLMConfig:
    mode: str = "stub"
    stub_profile: str = "mvp_default"
    temperature: float = 0.4
    timeout_s: int = 120


@dataclass
class AIMockConfig:
    enabled: bool = True
    version: str = "1.27.1"
    host: str = "127.0.0.1"
    port: int = 4010
    strict: bool = True
    metrics: bool = True
    fixture_dir: str = "tests/system_verify/llm_stub/aimock/fixtures"
    profile_dir: str = "tests/system_verify/llm_stub/aimock/profiles"
    seed: int = 20260522
    startup_timeout_s: int = 20
    api_key: str = "aimock-test-key"
    model: str = "deepseek-v4-flash"


@dataclass
class LLMStubConfig:
    aimock: AIMockConfig = field(default_factory=AIMockConfig)


READING_STOP_CROSS_CHAPTER = "cross_chapter"
READING_STOP_COMMENT_WINDOWS = "comment_windows"
READING_STOP_MODES = frozenset({READING_STOP_CROSS_CHAPTER, READING_STOP_COMMENT_WINDOWS})


@dataclass
class RealLLMLongFlowConfig:
    require_compaction: bool = True
    test_compaction_trigger_tokens: int = 24000
    test_compaction_min_source_tokens: int = 16000
    test_compaction_min_source_paragraphs: int = 120
    min_comment_windows: int = 2
    min_chat_turns: int = 1
    reading_stop_mode: str = READING_STOP_COMMENT_WINDOWS


@dataclass
class RealLLMConfig:
    base_url: str = ""
    api_key_env: str = "VIBE_READER_LLM_API_KEY"
    model: str = "deepseek-v4-flash"
    max_calls: int = 16
    max_input_tokens_per_call: int = 64000
    max_output_tokens_per_call: int = 1200
    max_total_cost_usd: float = 3.00
    long_flow: RealLLMLongFlowConfig = field(default_factory=RealLLMLongFlowConfig)

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
    collect_provider_usage: bool = False


@dataclass
class AuditConfig:
    sample_comments_per_window: int = 3
    sample_chat_turns_per_probe: int = 2
    include_prompt_manifest: bool = True
    include_full_prompt: bool = False
    include_original_excerpts: bool = True


@dataclass
class CommentDensityConfig:
    soft_min: float = 0.25
    stat_window_paragraphs: int = 80


@dataclass
class VerifyConfig:
    target: TargetConfig = field(default_factory=TargetConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    llm_stub: LLMStubConfig = field(default_factory=LLMStubConfig)
    real_llm: RealLLMConfig = field(default_factory=RealLLMConfig)
    run: RunConfig = field(default_factory=RunConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    comment_density: CommentDensityConfig = field(default_factory=CommentDensityConfig)

    @property
    def target_data_dir(self) -> pathlib.Path:
        return pathlib.Path(self.target.data_dir)

    @property
    def is_real_llm(self) -> bool:
        return self.llm.mode == "real"

    @property
    def usage_source(self) -> str:
        if self.is_real_llm and self.metrics.collect_provider_usage:
            return "provider"
        return "estimate"

    def llm_metric_tags(self) -> dict[str, object]:
        return {
            "llm_mode": self.llm.mode,
            "stub_profile": self.llm.stub_profile if not self.is_real_llm else None,
            "usage_source": self.usage_source,
            "real_llm": self.is_real_llm,
        }

    def effective_model(self) -> str | None:
        if self.is_real_llm:
            return _env("VIBE_READER_LLM_MODEL") or self.real_llm.model
        return self.llm_stub.aimock.model


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
    llm_mode = _env("VIBE_READER_VERIFY_LLM_MODE") or llm_raw.get("mode", "stub")
    llm = LLMConfig(
        mode=llm_mode,
        stub_profile=llm_raw.get("stub_profile", "mvp_default"),
        temperature=llm_raw.get("temperature", 0.4),
        timeout_s=llm_raw.get("timeout_s", 120),
    )

    aimock_raw = raw.get("llm_stub", {}).get("aimock", {})
    llm_stub = LLMStubConfig(
        aimock=AIMockConfig(
            enabled=aimock_raw.get("enabled", True),
            version=aimock_raw.get("version", "1.27.1"),
            host=aimock_raw.get("host", "127.0.0.1"),
            port=int(aimock_raw.get("port", 4010)),
            strict=aimock_raw.get("strict", True),
            metrics=aimock_raw.get("metrics", True),
            fixture_dir=aimock_raw.get(
                "fixture_dir", "tests/system_verify/llm_stub/aimock/fixtures"
            ),
            profile_dir=aimock_raw.get(
                "profile_dir", "tests/system_verify/llm_stub/aimock/profiles"
            ),
            seed=int(aimock_raw.get("seed", raw.get("run", {}).get("seed", 20260522))),
            startup_timeout_s=int(aimock_raw.get("startup_timeout_s", 20)),
            api_key=aimock_raw.get("api_key", "aimock-test-key"),
            model=aimock_raw.get("model", "deepseek-v4-flash"),
        )
    )

    real_raw = raw.get("real_llm", {})
    long_flow_raw = real_raw.get("long_flow", {})
    real_llm = RealLLMConfig(
        base_url=_env("VIBE_READER_LLM_BASE_URL") or real_raw.get("base_url", ""),
        api_key_env=real_raw.get("api_key_env", "VIBE_READER_LLM_API_KEY"),
        model=_env("VIBE_READER_LLM_MODEL")
        or real_raw.get("model", "deepseek-v4-flash"),
        max_calls=real_raw.get("max_calls", 16),
        max_input_tokens_per_call=real_raw.get("max_input_tokens_per_call", 64000),
        max_output_tokens_per_call=real_raw.get("max_output_tokens_per_call", 1200),
        max_total_cost_usd=real_raw.get("max_total_cost_usd", 3.00),
        long_flow=RealLLMLongFlowConfig(
            require_compaction=long_flow_raw.get("require_compaction", True),
            test_compaction_trigger_tokens=long_flow_raw.get(
                "test_compaction_trigger_tokens", 24000
            ),
            test_compaction_min_source_tokens=long_flow_raw.get(
                "test_compaction_min_source_tokens", 16000
            ),
            test_compaction_min_source_paragraphs=long_flow_raw.get(
                "test_compaction_min_source_paragraphs", 120
            ),
            min_comment_windows=long_flow_raw.get("min_comment_windows", 2),
            min_chat_turns=long_flow_raw.get("min_chat_turns", 1),
            reading_stop_mode=long_flow_raw.get(
                "reading_stop_mode", READING_STOP_COMMENT_WINDOWS
            ),
        ),
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
    default_collect_provider = llm.mode == "real"
    metrics = MetricsConfig(
        collect_otel=metrics_raw.get("collect_otel", True),
        collect_logfire=metrics_raw.get("collect_logfire", True),
        collect_sse_events=metrics_raw.get("collect_sse_events", True),
        collect_provider_usage=metrics_raw.get(
            "collect_provider_usage", default_collect_provider
        ),
    )

    audit_raw = raw.get("audit", {})
    audit = AuditConfig(
        sample_comments_per_window=audit_raw.get("sample_comments_per_window", 3),
        sample_chat_turns_per_probe=audit_raw.get("sample_chat_turns_per_probe", 2),
        include_prompt_manifest=audit_raw.get("include_prompt_manifest", True),
        include_full_prompt=audit_raw.get("include_full_prompt", False),
        include_original_excerpts=audit_raw.get("include_original_excerpts", True),
    )

    density_raw = raw.get("comment_density", {})
    comment_density = CommentDensityConfig(
        soft_min=density_raw.get("soft_min", 0.25),
        stat_window_paragraphs=density_raw.get("stat_window_paragraphs", 80),
    )

    return VerifyConfig(
        target=target,
        llm=llm,
        llm_stub=llm_stub,
        real_llm=real_llm,
        run=run,
        metrics=metrics,
        audit=audit,
        comment_density=comment_density,
    )


def validate_real_llm_config(config: VerifyConfig) -> list[str]:
    """Return configuration errors when real LLM mode is requested."""
    if not config.is_real_llm:
        return []

    errors: list[str] = []
    if not config.real_llm.base_url:
        errors.append("real_llm.base_url is required when llm.mode=real")
    if not config.real_llm.api_key:
        errors.append(
            f"real LLM API key env {config.real_llm.api_key_env} is required "
            "when llm.mode=real"
        )
    if not config.real_llm.model:
        errors.append("real_llm.model is required when llm.mode=real")
    stop_mode = config.real_llm.long_flow.reading_stop_mode
    if stop_mode not in READING_STOP_MODES:
        errors.append(
            "real_llm.long_flow.reading_stop_mode must be one of "
            f"{sorted(READING_STOP_MODES)}; got {stop_mode!r}"
        )
    return errors
