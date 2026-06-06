from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

import toml


def _default_data_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".vibe_reader"


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def _override_env(key: str, value: Any = None) -> Any:
    env_value = _env(key)
    return env_value if env_value is not None else value


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return list(default)


def _normalized_log_format(value: Any, *, default_json: bool = True) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"json", "text"}:
            return normalized
    return "json" if default_json else "text"


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
    provider_context_limit_tokens: int = 1_000_000
    attention_target_input_tokens: int = 128_000
    normal_target_input_tokens: int = 112_000
    compression_target_input_tokens: int = 128_000
    emergency_input_cap_tokens: int = 160_000
    reserved_tokens: int = 12_000
    target_chapter_summary_tokens: int = 7_000
    max_chapter_summary_tokens: int = 10_000
    max_anchor_excerpts: int = 12
    max_anchor_excerpt_tokens: int = 120
    max_context_jump_chars: int = 24_000
    max_context_jump_tokens_estimate: int = 24_000


@dataclass
class ContextL2Config:
    target_chunk_tokens: int = 24_000
    min_chunk_tokens: int = 18_000
    max_chunk_tokens: int = 32_000
    max_chunk_chars: int = 8_000
    max_chunk_paragraphs: int = 180
    target_live_original_tokens: int = 96_000
    max_live_original_tokens: int = 112_000
    min_live_chunks_after_compaction: int = 2
    compaction_reclaim_chunk_count: int = 1


@dataclass
class WindowL1Config:
    focus_target_tokens: int = 6_000
    focus_max_tokens: int = 12_000
    min_focus_paragraphs: int = 8
    max_focus_paragraphs: int = 40
    overlap_paragraphs: int = 4
    trigger_advance_ratio: float = 0.70
    comment_density_soft_min: float = 0.25
    comment_density_stat_window_paragraphs: int = 80


@dataclass
class ContextL3Config:
    preflight_trigger_input_tokens: int = 112_000
    compression_trigger_input_tokens: int = 128_000
    max_completed_l2_chunks_before_compaction: int = 4
    min_completed_l2_chunks_before_compaction: int = 3
    compaction_reclaim_chunk_count: int = 1
    compaction_timeout_s: int = 180
    allow_emergency_overflow_once: bool = True


@dataclass
class EphemeralCommentsConfig:
    recent_focus_windows: int = 3
    nearby_paragraph_margin: int = 20
    max_tokens: int = 3_000
    compress: bool = False


@dataclass
class EphemeralChatConfig:
    recent_turns: int = 6
    max_tokens: int = 4_000
    compress: bool = False
    scope: str = "current_session"


@dataclass
class ObservabilityConsoleConfig:
    enabled: bool = True
    stream: str = "stdout"


@dataclass
class ObservabilityFileConfig:
    enabled: bool = False
    path: str = ""
    max_bytes: int = 10_485_760
    backup_count: int = 5


@dataclass
class ObservabilityOtelConfig:
    enabled: bool = False
    endpoint: str = ""
    protocol: str = "otlp_http"
    export_traces: bool = True
    export_metrics: bool = True
    export_logs: bool = False
    sample_ratio: float = 1.0


@dataclass
class ObservabilityAuditConfig:
    enabled: bool = False
    include_prompt_manifest: bool = True
    include_full_prompt: bool = False
    include_model_response: bool = False
    redact_secrets: bool = True


@dataclass
class ObservabilityConfig:
    enabled: bool = True
    provider: str = "otel"
    log_json: bool = True
    log_format: str = "json"
    log_sinks: list[str] = field(default_factory=lambda: ["console"])
    log_level: str = "INFO"
    environment: str = "local"
    include_prompt_manifest: bool = True
    include_full_prompt: bool = False
    service_name: str = "vibe-reader-backend"
    otel_endpoint: str = ""
    console: ObservabilityConsoleConfig = field(
        default_factory=ObservabilityConsoleConfig
    )
    file: ObservabilityFileConfig = field(default_factory=ObservabilityFileConfig)
    otel: ObservabilityOtelConfig = field(default_factory=ObservabilityOtelConfig)
    audit: ObservabilityAuditConfig = field(default_factory=ObservabilityAuditConfig)


@dataclass
class TokenEstimationConfig:
    token_safety_margin: float = 1.10
    calibration_percentile: float = 0.95
    calibration_window_size: int = 50
    min_calibration_samples: int = 5
    default_bootstrap_calibration_ratio: float = 1.0


@dataclass
class Settings:
    data_dir: pathlib.Path = field(default_factory=_default_data_dir)
    llm: LLMConfig = field(default_factory=LLMConfig)
    reader: ReaderConfig = field(default_factory=ReaderConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    context_l2: ContextL2Config = field(default_factory=ContextL2Config)
    window_l1: WindowL1Config = field(default_factory=WindowL1Config)
    context_l3: ContextL3Config = field(default_factory=ContextL3Config)
    ephemeral_comments: EphemeralCommentsConfig = field(
        default_factory=EphemeralCommentsConfig
    )
    ephemeral_chat: EphemeralChatConfig = field(default_factory=EphemeralChatConfig)
    token_estimation: TokenEstimationConfig = field(
        default_factory=TokenEstimationConfig
    )
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
        provider_context_limit_tokens=ctx_raw.get(
            "provider_context_limit_tokens", 1_000_000
        ),
        attention_target_input_tokens=ctx_raw.get(
            "attention_target_input_tokens", 128_000
        ),
        normal_target_input_tokens=ctx_raw.get("normal_target_input_tokens", 112_000),
        compression_target_input_tokens=ctx_raw.get(
            "compression_target_input_tokens", 128_000
        ),
        emergency_input_cap_tokens=ctx_raw.get("emergency_input_cap_tokens", 160_000),
        reserved_tokens=ctx_raw.get("reserved_tokens", 12_000),
        target_chapter_summary_tokens=ctx_raw.get(
            "target_chapter_summary_tokens", 7_000
        ),
        max_chapter_summary_tokens=ctx_raw.get("max_chapter_summary_tokens", 10_000),
        max_anchor_excerpts=ctx_raw.get("max_anchor_excerpts", 12),
        max_anchor_excerpt_tokens=ctx_raw.get("max_anchor_excerpt_tokens", 120),
        max_context_jump_chars=ctx_raw.get("max_context_jump_chars", 24_000),
        max_context_jump_tokens_estimate=ctx_raw.get(
            "max_context_jump_tokens_estimate", 24_000
        ),
    )

    ctx_l2_raw = raw.get("context_l2", {})
    context_l2 = ContextL2Config(
        target_chunk_tokens=ctx_l2_raw.get("target_chunk_tokens", 24_000),
        min_chunk_tokens=ctx_l2_raw.get("min_chunk_tokens", 18_000),
        max_chunk_tokens=ctx_l2_raw.get("max_chunk_tokens", 32_000),
        max_chunk_chars=ctx_l2_raw.get("max_chunk_chars", 8_000),
        max_chunk_paragraphs=ctx_l2_raw.get("max_chunk_paragraphs", 180),
        target_live_original_tokens=ctx_l2_raw.get(
            "target_live_original_tokens", 96_000
        ),
        max_live_original_tokens=ctx_l2_raw.get("max_live_original_tokens", 112_000),
        min_live_chunks_after_compaction=ctx_l2_raw.get(
            "min_live_chunks_after_compaction", 2
        ),
        compaction_reclaim_chunk_count=ctx_l2_raw.get(
            "compaction_reclaim_chunk_count", 1
        ),
    )

    win_raw = raw.get("window_l1", {})
    window_l1 = WindowL1Config(
        focus_target_tokens=win_raw.get("focus_target_tokens", 6_000),
        focus_max_tokens=win_raw.get("focus_max_tokens", 12_000),
        min_focus_paragraphs=win_raw.get("min_focus_paragraphs", 8),
        max_focus_paragraphs=win_raw.get("max_focus_paragraphs", 40),
        overlap_paragraphs=win_raw.get("overlap_paragraphs", 4),
        trigger_advance_ratio=win_raw.get("trigger_advance_ratio", 0.70),
        comment_density_soft_min=win_raw.get("comment_density_soft_min", 0.25),
        comment_density_stat_window_paragraphs=win_raw.get(
            "comment_density_stat_window_paragraphs", 80
        ),
    )

    ctx_l3_raw = raw.get("context_l3", {})
    context_l3 = ContextL3Config(
        preflight_trigger_input_tokens=ctx_l3_raw.get(
            "preflight_trigger_input_tokens", 112_000
        ),
        compression_trigger_input_tokens=ctx_l3_raw.get(
            "compression_trigger_input_tokens", 128_000
        ),
        max_completed_l2_chunks_before_compaction=ctx_l3_raw.get(
            "max_completed_l2_chunks_before_compaction", 4
        ),
        min_completed_l2_chunks_before_compaction=ctx_l3_raw.get(
            "min_completed_l2_chunks_before_compaction", 3
        ),
        compaction_reclaim_chunk_count=ctx_l3_raw.get(
            "compaction_reclaim_chunk_count", 1
        ),
        compaction_timeout_s=ctx_l3_raw.get("compaction_timeout_s", 180),
        allow_emergency_overflow_once=ctx_l3_raw.get(
            "allow_emergency_overflow_once", True
        ),
    )

    eph_comments_raw = raw.get("ephemeral_comments", {})
    ephemeral_comments = EphemeralCommentsConfig(
        recent_focus_windows=eph_comments_raw.get("recent_focus_windows", 3),
        nearby_paragraph_margin=eph_comments_raw.get("nearby_paragraph_margin", 20),
        max_tokens=eph_comments_raw.get("max_tokens", 3_000),
        compress=eph_comments_raw.get("compress", False),
    )

    eph_chat_raw = raw.get("ephemeral_chat", {})
    ephemeral_chat = EphemeralChatConfig(
        recent_turns=eph_chat_raw.get("recent_turns", 6),
        max_tokens=eph_chat_raw.get("max_tokens", 4_000),
        compress=eph_chat_raw.get("compress", False),
        scope=eph_chat_raw.get("scope", "current_session"),
    )

    te_raw = raw.get("token_estimation", {})
    token_estimation = TokenEstimationConfig(
        token_safety_margin=te_raw.get("token_safety_margin", 1.10),
        calibration_percentile=te_raw.get("calibration_percentile", 0.95),
        calibration_window_size=te_raw.get("calibration_window_size", 50),
        min_calibration_samples=te_raw.get("min_calibration_samples", 5),
        default_bootstrap_calibration_ratio=te_raw.get(
            "default_bootstrap_calibration_ratio", 1.0
        ),
    )

    obs_raw = raw.get("observability", {})
    obs_console_raw = obs_raw.get("console", {})
    obs_file_raw = obs_raw.get("file", {})
    obs_otel_raw = obs_raw.get("otel", {})
    obs_audit_raw = obs_raw.get("audit", {})
    log_json_default = _as_bool(obs_raw.get("log_json"), True)
    log_format = _normalized_log_format(
        _override_env("VIBE_READER_LOG_FORMAT", obs_raw.get("log_format")),
        default_json=log_json_default,
    )
    log_sinks = _as_str_list(
        _override_env("VIBE_READER_LOG_SINKS", obs_raw.get("log_sinks")),
        ["console"],
    )
    otel_endpoint = (
        _override_env(
            "VIBE_READER_OTEL_ENDPOINT",
            _first_not_none(
                obs_otel_raw.get("endpoint"),
                obs_raw.get("endpoint"),
                "",
            ),
        )
    )
    otel = ObservabilityOtelConfig(
        enabled=_as_bool(
            _override_env(
                "VIBE_READER_OTEL_ENABLED",
                _first_not_none(
                    obs_otel_raw.get("enabled"),
                    obs_raw.get("otel_enabled"),
                ),
            ),
            bool(otel_endpoint),
        ),
        endpoint=otel_endpoint,
        protocol=obs_otel_raw.get("protocol", "otlp_http"),
        export_traces=_as_bool(
            _override_env(
                "VIBE_READER_OTEL_EXPORT_TRACES",
                obs_otel_raw.get("export_traces"),
            ),
            True,
        ),
        export_metrics=_as_bool(
            _override_env(
                "VIBE_READER_OTEL_EXPORT_METRICS",
                obs_otel_raw.get("export_metrics"),
            ),
            True,
        ),
        export_logs=_as_bool(
            _override_env(
                "VIBE_READER_OTEL_EXPORT_LOGS",
                obs_otel_raw.get("export_logs"),
            ),
            False,
        ),
        sample_ratio=_as_float(
            _override_env(
                "VIBE_READER_OTEL_SAMPLE_RATIO",
                obs_otel_raw.get("sample_ratio"),
            ),
            1.0,
        ),
    )
    audit = ObservabilityAuditConfig(
        enabled=_as_bool(obs_audit_raw.get("enabled"), False),
        include_prompt_manifest=_as_bool(
            obs_audit_raw.get(
                "include_prompt_manifest",
                obs_raw.get("include_prompt_manifest"),
            ),
            True,
        ),
        include_full_prompt=_as_bool(
            obs_audit_raw.get("include_full_prompt", obs_raw.get("include_full_prompt")),
            False,
        ),
        include_model_response=_as_bool(
            obs_audit_raw.get("include_model_response"),
            False,
        ),
        redact_secrets=_as_bool(obs_audit_raw.get("redact_secrets"), True),
    )
    observability = ObservabilityConfig(
        enabled=_as_bool(
            _override_env("VIBE_READER_OBSERVABILITY_ENABLED", obs_raw.get("enabled")),
            True,
        ),
        provider=obs_raw.get("provider", "otel"),
        log_json=log_format == "json",
        log_format=log_format,
        log_sinks=log_sinks,
        log_level=_override_env(
            "VIBE_READER_LOG_LEVEL",
            obs_raw.get("log_level", "INFO"),
        ),
        environment=_override_env(
            "VIBE_READER_ENVIRONMENT",
            obs_raw.get("environment", "local"),
        ),
        include_prompt_manifest=audit.include_prompt_manifest,
        include_full_prompt=audit.include_full_prompt,
        service_name=obs_raw.get("service_name", "vibe-reader-backend")
        if isinstance(obs_raw.get("service_name"), str)
        else "vibe-reader-backend",
        otel_endpoint=otel.endpoint,
        console=ObservabilityConsoleConfig(
            enabled=_as_bool(obs_console_raw.get("enabled"), True),
            stream=obs_console_raw.get("stream", "stdout"),
        ),
        file=ObservabilityFileConfig(
            enabled=_as_bool(obs_file_raw.get("enabled"), "file" in log_sinks),
            path=obs_file_raw.get("path", ""),
            max_bytes=_as_int(obs_file_raw.get("max_bytes"), 10_485_760),
            backup_count=_as_int(obs_file_raw.get("backup_count"), 5),
        ),
        otel=otel,
        audit=audit,
    )

    verify_mode = _as_bool(_env("VIBE_READER_VERIFY_MODE"), False)

    return Settings(
        data_dir=data_dir,
        llm=llm,
        reader=reader,
        context=context,
        context_l2=context_l2,
        window_l1=window_l1,
        context_l3=context_l3,
        ephemeral_comments=ephemeral_comments,
        ephemeral_chat=ephemeral_chat,
        token_estimation=token_estimation,
        observability=observability,
        verify_mode=verify_mode,
    )
