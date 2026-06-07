from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any


DEFAULT_LLM_MODEL = "deepseek-v4-flash"
DEFAULT_LLM_PROVIDER = "openai_compatible"
DEFAULT_MODEL_ID = "default"
MASKED_SECRET = "********"
SECRET_UNCHANGED_SENTINEL = "__vibe_reader_secret_unchanged__"
THINK_EFFORT_VALUES = frozenset(("", "minimal", "low", "medium", "high"))
PERSISTED_SETTINGS_GROUPS = (
    "reader",
    "context",
    "context_l2",
    "window_l1",
    "context_l3",
    "ephemeral_comments",
    "ephemeral_chat",
    "token_estimation",
    "observability",
)

LLM_ENV_KEYS = {
    "VIBE_READER_LLM_BASE_URL": "llm.base_url",
    "VIBE_READER_LLM_API_KEY": "llm.api_key",
    "VIBE_READER_LLM_MODEL": "llm.model",
}

NON_LLM_ENV_FIELD_PATHS = {
    "VIBE_READER_DATA_DIR": "data_dir",
    "VIBE_READER_OBSERVABILITY_ENABLED": "observability.enabled",
    "VIBE_READER_LOG_LEVEL": "observability.log_level",
    "VIBE_READER_LOG_FORMAT": "observability.log_format",
    "VIBE_READER_LOG_SINKS": "observability.log_sinks",
    "VIBE_READER_OTEL_ENDPOINT": "observability.otel.endpoint",
    "VIBE_READER_OTEL_ENABLED": "observability.otel.enabled",
    "VIBE_READER_OTEL_EXPORT_TRACES": "observability.otel.export_traces",
    "VIBE_READER_OTEL_EXPORT_METRICS": "observability.otel.export_metrics",
    "VIBE_READER_OTEL_EXPORT_LOGS": "observability.otel.export_logs",
    "VIBE_READER_OTEL_SAMPLE_RATIO": "observability.otel.sample_ratio",
    "VIBE_READER_ENVIRONMENT": "observability.environment",
    "VIBE_READER_VERIFY_MODE": "verify_mode",
}


def _default_data_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".vibe_reader"


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = DEFAULT_LLM_MODEL
    provider: str = DEFAULT_LLM_PROVIDER
    model_id: str = ""
    think_effort: str = ""
    source: str = "default"

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def calibration_identity(self) -> str:
        if self.model_id:
            return f"{self.provider}:{self.model_id}:{self.model}"
        return f"{self.provider}:{self.model}"


@dataclass
class ModelConfig:
    id: str = DEFAULT_MODEL_ID
    provider: str = DEFAULT_LLM_PROVIDER
    url: str = ""
    model_name: str = DEFAULT_LLM_MODEL
    api_key: str = ""
    think_effort: str = ""

    def to_llm(self, *, source: str = "catalog") -> LLMConfig:
        return LLMConfig(
            base_url=self.url,
            api_key=self.api_key,
            model=self.model_name,
            provider=self.provider,
            model_id=self.id,
            think_effort=self.think_effort,
            source=source,
        )

    @property
    def calibration_identity(self) -> str:
        return self.to_llm().calibration_identity


@dataclass
class ModelDefaultsConfig:
    global_model_id: str = ""
    chat_model_id: str = ""
    comment_model_id: str = ""


@dataclass
class ActiveModelsConfig:
    global_model_id: str = ""
    chat_model_id: str = ""
    comment_model_id: str = ""


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


PERSISTED_SETTINGS_GROUP_TYPES: dict[str, type[Any]] = {
    "reader": ReaderConfig,
    "context": ContextConfig,
    "context_l2": ContextL2Config,
    "window_l1": WindowL1Config,
    "context_l3": ContextL3Config,
    "ephemeral_comments": EphemeralCommentsConfig,
    "ephemeral_chat": EphemeralChatConfig,
    "token_estimation": TokenEstimationConfig,
    "observability": ObservabilityConfig,
}


@dataclass
class Settings:
    data_dir: pathlib.Path = field(default_factory=_default_data_dir)
    llm: LLMConfig = field(default_factory=LLMConfig)
    models: list[ModelConfig] = field(default_factory=list)
    defaults: ModelDefaultsConfig = field(default_factory=ModelDefaultsConfig)
    active: ActiveModelsConfig = field(default_factory=ActiveModelsConfig)
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
    env_overrides: dict[str, str] = field(default_factory=dict)
    ignored_env: dict[str, list[str]] = field(default_factory=dict)
    read_only_env: dict[str, list[str]] = field(default_factory=dict)
    migrations: list[str] = field(default_factory=list)

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

    @property
    def model_catalog(self) -> dict[str, ModelConfig]:
        return {model.id: model for model in self.models}

    def resolve_model_id(self, agent: str = "global") -> str:
        catalog = self.model_catalog
        if not catalog:
            return ""

        normalized_agent = _normalize_agent_key(agent)
        if normalized_agent == "chat":
            candidates = (
                self.active.chat_model_id,
                self.defaults.chat_model_id,
                self.active.global_model_id,
                self.defaults.global_model_id,
            )
        elif normalized_agent in {"comment", "compaction"}:
            candidates = (
                self.active.comment_model_id,
                self.defaults.comment_model_id,
                self.active.global_model_id,
                self.defaults.global_model_id,
            )
        else:
            candidates = (
                self.active.global_model_id,
                self.defaults.global_model_id,
            )

        for model_id in candidates:
            if model_id in catalog:
                return model_id
        return self.models[0].id

    def effective_model(self, agent: str = "global") -> ModelConfig | None:
        model_id = self.resolve_model_id(agent)
        return self.model_catalog.get(model_id)

    def effective_llm(self, agent: str = "global") -> LLMConfig:
        model = self.effective_model(agent)
        if model is None:
            return self.llm
        return model.to_llm()

    def effective_model_identity(self, agent: str = "global") -> str:
        return self.effective_llm(agent).calibration_identity

    def public_models(self) -> list[dict[str, Any]]:
        return [public_model_config(model) for model in self.models]

    def ui_metadata(self) -> dict[str, Any]:
        from .config_metadata import build_settings_metadata

        return build_settings_metadata(self)


def _normalize_agent_key(agent: str) -> str:
    normalized = (agent or "global").strip()
    mapping = {
        "ReadingChatAgent": "chat",
        "ParagraphCommentAgent": "comment",
        "ContextCompactionAgent": "comment",
        "compaction": "comment",
    }
    return mapping.get(normalized, normalized)


def _coerce_model_id(value: Any, fallback: str = DEFAULT_MODEL_ID) -> str:
    text = str(value or "").strip()
    return text or fallback


def _coerce_provider(value: Any) -> str:
    text = str(value or "").strip()
    return text or DEFAULT_LLM_PROVIDER


def _coerce_think_effort(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in THINK_EFFORT_VALUES else ""


def mask_secret(value: str) -> str:
    return MASKED_SECRET if value else ""


def is_secret_unchanged(value: Any) -> bool:
    text = str(value or "")
    return text in {MASKED_SECRET, SECRET_UNCHANGED_SENTINEL}


def public_model_config(model: ModelConfig) -> dict[str, Any]:
    return {
        "id": model.id,
        "provider": model.provider,
        "url": model.url,
        "model_name": model.model_name,
        "api_key_configured": bool(model.api_key),
        "api_key": mask_secret(model.api_key),
        "think_effort": model.think_effort,
    }


def merge_model_update(
    existing: ModelConfig | None,
    update: dict[str, Any],
) -> ModelConfig:
    base = existing or ModelConfig()
    api_key = base.api_key
    if "api_key" in update and not is_secret_unchanged(update.get("api_key")):
        api_key = str(update.get("api_key") or "")

    return ModelConfig(
        id=_coerce_model_id(update.get("id", base.id), base.id),
        provider=_coerce_provider(update.get("provider", base.provider)),
        url=str(update.get("url", update.get("base_url", base.url)) or ""),
        model_name=str(
            update.get("model_name", update.get("model", base.model_name))
            or DEFAULT_LLM_MODEL
        ),
        api_key=api_key,
        think_effort=_coerce_think_effort(
            update.get("think_effort", base.think_effort)
        ),
    )
