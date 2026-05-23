from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

import toml


def _default_data_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".vibe_reader"


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = "deepseek-v4-flash"


@dataclass
class ReaderConfig:
    lookahead_paragraphs: int = 5
    progress_debounce_ms: int = 800


@dataclass
class ContextConfig:
    effective_input_budget: int = 128_000
    normal_target_input: int = 110_000
    hard_input_cap: int = 120_000
    reserved_budget: int = 12_000
    recent_chat_turns: int = 6
    recent_comments: int = 30


@dataclass
class WindowConfig:
    target_window_tokens: int = 6000
    max_window_tokens: int = 12_000
    min_window_paragraphs: int = 8
    max_window_paragraphs: int = 40
    overlap_paragraphs: int = 4
    trigger_advance_ratio: float = 0.70
    comment_density_soft_min: float = 0.25
    comment_density_stat_window_paragraphs: int = 80


@dataclass
class ObservabilityConfig:
    enabled: bool = True
    provider: str = "otel"
    log_json: bool = True
    log_level: str = "INFO"
    include_prompt_manifest: bool = True
    include_full_prompt: bool = False
    service_name: str = "vibe-reader-backend"
    otel_endpoint: str = ""


@dataclass
class Settings:
    data_dir: pathlib.Path = field(default_factory=_default_data_dir)
    llm: LLMConfig = field(default_factory=LLMConfig)
    reader: ReaderConfig = field(default_factory=ReaderConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    verify_mode: bool = False

    @property
    def db_path(self) -> pathlib.Path:
        return self.data_dir / "vibe_reader.db"

    @property
    def books_dir(self) -> pathlib.Path:
        return self.data_dir / "books"

    @property
    def logs_dir(self) -> pathlib.Path:
        return self.data_dir / "logs"

    @property
    def config_path(self) -> pathlib.Path:
        return self.data_dir / "config.toml"


def load_settings() -> Settings:
    data_dir = pathlib.Path(_env("VIBE_READER_DATA_DIR") or str(_default_data_dir()))
    config_path = data_dir / "config.toml"

    raw: dict = {}
    if config_path.exists():
        raw = toml.load(config_path)

    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        base_url=_env("VIBE_READER_LLM_BASE_URL") or llm_raw.get("base_url", ""),
        api_key=_env("VIBE_READER_LLM_API_KEY") or llm_raw.get("api_key", ""),
        model=_env("VIBE_READER_LLM_MODEL")
        or llm_raw.get("model", "deepseek-v4-flash"),
    )

    reader_raw = raw.get("reader", {})
    reader = ReaderConfig(
        lookahead_paragraphs=reader_raw.get("lookahead_paragraphs", 5),
        progress_debounce_ms=reader_raw.get("progress_debounce_ms", 800),
    )

    ctx_raw = raw.get("context", {})
    context = ContextConfig(
        effective_input_budget=ctx_raw.get("effective_input_budget", 128_000),
        normal_target_input=ctx_raw.get("normal_target_input", 110_000),
        hard_input_cap=ctx_raw.get("hard_input_cap", 120_000),
        reserved_budget=ctx_raw.get("reserved_budget", 12_000),
        recent_chat_turns=ctx_raw.get("recent_chat_turns", 6),
        recent_comments=ctx_raw.get("recent_comments", 30),
    )

    win_raw = raw.get("window", {})
    window = WindowConfig(
        target_window_tokens=win_raw.get("target_window_tokens", 6000),
        max_window_tokens=win_raw.get("max_window_tokens", 12_000),
        min_window_paragraphs=win_raw.get("min_window_paragraphs", 8),
        max_window_paragraphs=win_raw.get("max_window_paragraphs", 40),
        overlap_paragraphs=win_raw.get("overlap_paragraphs", 4),
        trigger_advance_ratio=win_raw.get("trigger_advance_ratio", 0.70),
        comment_density_soft_min=win_raw.get("comment_density_soft_min", 0.25),
        comment_density_stat_window_paragraphs=win_raw.get(
            "comment_density_stat_window_paragraphs", 80
        ),
    )

    obs_raw = raw.get("observability", {})
    observability = ObservabilityConfig(
        enabled=_env("VIBE_READER_OBSERVABILITY_ENABLED") not in ("0", "false", "")
        if _env("VIBE_READER_OBSERVABILITY_ENABLED") is not None
        else obs_raw.get("enabled", True),
        provider=obs_raw.get("provider", "otel"),
        log_json=obs_raw.get("log_json", True),
        log_level=_env("VIBE_READER_LOG_LEVEL") or obs_raw.get("log_level", "INFO"),
        include_prompt_manifest=obs_raw.get("include_prompt_manifest", True),
        include_full_prompt=obs_raw.get("include_full_prompt", False),
        service_name=obs_raw.get("service_name", "vibe-reader-backend")
        if isinstance(obs_raw.get("service_name"), str)
        else "vibe-reader-backend",
        otel_endpoint=_env("VIBE_READER_OTEL_ENDPOINT") or obs_raw.get("endpoint", ""),
    )

    verify_mode = _env("VIBE_READER_VERIFY_MODE") in ("1", "true")

    return Settings(
        data_dir=data_dir,
        llm=llm,
        reader=reader,
        context=context,
        window=window,
        observability=observability,
        verify_mode=verify_mode,
    )
