from __future__ import annotations

import os
import pathlib
import tempfile
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from typing import Any

import toml


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


def _env_values(keys: dict[str, str]) -> dict[str, str]:
    return {key: os.environ[key] for key in keys if key in os.environ}


def _env_override_fields() -> dict[str, str]:
    return {
        path: env_key
        for env_key, path in NON_LLM_ENV_FIELD_PATHS.items()
        if _env(env_key) is not None
    }


def _has_legacy_llm(raw: dict[str, Any]) -> bool:
    llm_raw = raw.get("llm")
    return isinstance(llm_raw, dict) and any(
        key in llm_raw for key in ("base_url", "api_key", "model", "url", "model_name")
    )


def _coerce_model_id(value: Any, fallback: str = DEFAULT_MODEL_ID) -> str:
    text = str(value or "").strip()
    return text or fallback


def _coerce_provider(value: Any) -> str:
    text = str(value or "").strip()
    return text or DEFAULT_LLM_PROVIDER


def _coerce_think_effort(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in THINK_EFFORT_VALUES else ""


def _legacy_model_from_raw(raw: dict[str, Any]) -> ModelConfig:
    llm_raw = raw.get("llm", {})
    if not isinstance(llm_raw, dict):
        llm_raw = {}
    return ModelConfig(
        id=_coerce_model_id(llm_raw.get("id"), DEFAULT_MODEL_ID),
        provider=_coerce_provider(llm_raw.get("provider")),
        url=str(llm_raw.get("url", llm_raw.get("base_url", "")) or ""),
        model_name=str(
            llm_raw.get("model_name", llm_raw.get("model", DEFAULT_LLM_MODEL))
            or DEFAULT_LLM_MODEL
        ),
        api_key=str(llm_raw.get("api_key", "") or ""),
        think_effort=_coerce_think_effort(llm_raw.get("think_effort")),
    )


def _env_llm_config() -> LLMConfig:
    return LLMConfig(
        base_url=_env("VIBE_READER_LLM_BASE_URL") or "",
        api_key=_env("VIBE_READER_LLM_API_KEY") or "",
        model=_env("VIBE_READER_LLM_MODEL") or DEFAULT_LLM_MODEL,
        source="env",
    )


def _parse_model_catalog(raw_models: Any) -> list[ModelConfig]:
    if not isinstance(raw_models, list):
        return []

    models: list[ModelConfig] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_models, start=1):
        if not isinstance(item, dict):
            continue
        model_id = _coerce_model_id(item.get("id") or item.get("name"), f"model_{idx}")
        if model_id in seen:
            continue
        seen.add(model_id)
        models.append(
            ModelConfig(
                id=model_id,
                provider=_coerce_provider(item.get("provider")),
                url=str(item.get("url", item.get("base_url", "")) or ""),
                model_name=str(
                    item.get("model_name", item.get("model", DEFAULT_LLM_MODEL))
                    or DEFAULT_LLM_MODEL
                ),
                api_key=str(item.get("api_key", "") or ""),
                think_effort=_coerce_think_effort(item.get("think_effort")),
            )
        )
    return models


def _model_ref(value: Any, catalog: dict[str, ModelConfig], fallback: str = "") -> str:
    text = str(value or "").strip()
    if text and text in catalog:
        return text
    return fallback


def _load_model_refs(
    raw: dict[str, Any],
    models: list[ModelConfig],
) -> tuple[ModelDefaultsConfig, ActiveModelsConfig]:
    if not models:
        return ModelDefaultsConfig(), ActiveModelsConfig()

    catalog = {model.id: model for model in models}
    first_model_id = models[0].id
    defaults_raw = raw.get("defaults", {})
    active_raw = raw.get("active", {})
    if not isinstance(defaults_raw, dict):
        defaults_raw = {}
    if not isinstance(active_raw, dict):
        active_raw = {}

    global_default = _model_ref(
        defaults_raw.get("global_model_id") or defaults_raw.get("model_id"),
        catalog,
        first_model_id,
    )
    defaults = ModelDefaultsConfig(
        global_model_id=global_default,
        chat_model_id=_model_ref(
            defaults_raw.get("chat_model_id"), catalog, global_default
        ),
        comment_model_id=_model_ref(
            defaults_raw.get("comment_model_id"), catalog, global_default
        ),
    )
    active = ActiveModelsConfig(
        global_model_id=_model_ref(active_raw.get("global_model_id"), catalog, ""),
        chat_model_id=_model_ref(active_raw.get("chat_model_id"), catalog, ""),
        comment_model_id=_model_ref(active_raw.get("comment_model_id"), catalog, ""),
    )
    return defaults, active


def _model_to_toml(model: ModelConfig) -> dict[str, Any]:
    return {
        "id": model.id,
        "provider": model.provider,
        "url": model.url,
        "model_name": model.model_name,
        "api_key": model.api_key,
        "think_effort": model.think_effort,
    }


def _refs_to_toml(refs: ModelDefaultsConfig | ActiveModelsConfig) -> dict[str, Any]:
    return {
        "global_model_id": refs.global_model_id,
        "chat_model_id": refs.chat_model_id,
        "comment_model_id": refs.comment_model_id,
    }


def _model_sections(
    models: list[ModelConfig],
    defaults: ModelDefaultsConfig,
    active: ActiveModelsConfig,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "models": [_model_to_toml(model) for model in models],
        "defaults": _refs_to_toml(defaults),
    }
    active_data = _refs_to_toml(active)
    if any(active_data.values()):
        data["active"] = active_data
    return data


def _write_toml_user_only(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            toml.dump(data, fh)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _clean_and_write_model_config(
    config_path: pathlib.Path,
    raw: dict[str, Any],
    models: list[ModelConfig],
    defaults: ModelDefaultsConfig,
    active: ActiveModelsConfig,
) -> None:
    cleaned = dict(raw)
    cleaned.pop("llm", None)
    cleaned.update(_model_sections(models, defaults, active))
    _write_toml_user_only(config_path, cleaned)


def _settings_to_toml(settings: Settings) -> dict[str, Any]:
    data = _model_sections(settings.models, settings.defaults, settings.active)
    for group_name in PERSISTED_SETTINGS_GROUPS:
        data[group_name] = asdict(getattr(settings, group_name))
    return data


def save_settings(
    settings: Settings,
    path: pathlib.Path | None = None,
    *,
    reset_env_override_paths: set[str] | None = None,
) -> None:
    target_path = path or settings.config_path
    data = _settings_to_toml(settings)
    raw = toml.load(target_path) if target_path.exists() else {}
    _preserve_env_override_paths(
        data,
        settings=settings,
        raw=raw,
        reset_env_override_paths=reset_env_override_paths or set(),
    )
    _write_toml_user_only(target_path, data)


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


GROUP_INFO = {
    "models": ("模型管理", "维护可被 Chat、评论和压缩任务引用的 LLM 连接配置。"),
    "defaults": ("Agent 默认模型", "指定各 Agent 未临时切换时使用的模型目录条目。"),
    "active": ("当前生效模型", "记录运行时当前选择；空值表示沿用 Agent 默认或全局默认。"),
    "reader": ("阅读", "控制阅读位置推进、前瞻范围和进度写入节奏。"),
    "window_l1": ("窗口 L1", "控制当前阅读窗口大小、重叠和评论密度提示。"),
    "context": ("上下文", "控制供应商上下文上限、输入预算和锚点保留策略。"),
    "context_l2": ("上下文 L2", "控制原文 chunk 划分和活跃原文预算。"),
    "context_l3": ("上下文 L3 与压缩", "控制压缩触发阈值、回收策略和超时。"),
    "ephemeral_comments": ("评论临时上下文", "控制评论生成时附带的短期阅读状态。"),
    "ephemeral_chat": ("聊天临时上下文", "控制 Chat 请求附带的近期对话轮数和预算。"),
    "token_estimation": ("Token 估算", "控制本地 token 估算安全边际和校准窗口。"),
    "observability": ("可观测性", "控制日志、OTEL 和 Agent 审计输出。"),
}

FIELD_INFO: dict[str, dict[str, Any]] = {
    "models[].id": {
        "label": "模型 ID",
        "description": "模型目录中的唯一标识，用于默认模型和当前模型引用。",
        "type": "string",
        "constraints": {"required": True, "pattern": "非空唯一字符串"},
    },
    "models[].provider": {
        "label": "提供方",
        "description": "LLM 提供方类型；当前运行时使用 OpenAI 兼容协议，并预留扩展值。",
        "type": "enum",
        "constraints": {"default": DEFAULT_LLM_PROVIDER},
    },
    "models[].url": {
        "label": "API Base URL",
        "description": "OpenAI 兼容接口的 base URL，通常以 /v1 结尾。",
        "type": "string",
        "constraints": {"format": "url"},
    },
    "models[].model_name": {
        "label": "模型名称",
        "description": "发送给 provider 的模型名称。",
        "type": "string",
        "constraints": {"required": True},
    },
    "models[].api_key": {
        "label": "API Key",
        "description": "访问密钥；读取时只返回掩码，未修改保存时应保留原值。",
        "type": "secret",
        "constraints": {"masked_readback": MASKED_SECRET},
    },
    "models[].think_effort": {
        "label": "思考力度",
        "description": "供支持 reasoning/thinking 的模型使用；不支持时可留空。",
        "type": "enum",
        "constraints": {"values": sorted(THINK_EFFORT_VALUES)},
    },
    "defaults.global_model_id": {
        "label": "全局默认模型",
        "description": "未设置 Agent 默认时使用的模型目录引用。",
        "type": "model_ref",
    },
    "defaults.chat_model_id": {
        "label": "Chat 默认模型",
        "description": "ReadingChatAgent 默认使用的模型目录引用。",
        "type": "model_ref",
    },
    "defaults.comment_model_id": {
        "label": "评论默认模型",
        "description": "ParagraphCommentAgent 默认使用的模型；ContextCompactionAgent 与它共用。",
        "type": "model_ref",
    },
    "active.global_model_id": {
        "label": "全局当前模型",
        "description": "运行时当前全局模型；空值表示使用全局默认。",
        "type": "model_ref",
    },
    "active.chat_model_id": {
        "label": "Chat 当前模型",
        "description": "Chat 的运行时临时模型；空值表示使用 Chat 默认。",
        "type": "model_ref",
    },
    "active.comment_model_id": {
        "label": "评论当前模型",
        "description": "评论与压缩的运行时临时模型；空值表示使用评论默认。",
        "type": "model_ref",
    },
    "reader.lookahead_paragraphs": {
        "label": "前瞻段落数",
        "description": "阅读位置前方纳入助手处理范围的段落数量。",
        "constraints": {"min": 0},
    },
    "reader.progress_debounce_ms": {
        "label": "进度防抖毫秒",
        "description": "阅读进度写入和后台处理触发的防抖时间。",
        "constraints": {"min": 0},
    },
    "window_l1.focus_target_tokens": {
        "label": "焦点目标 token",
        "description": "当前阅读焦点窗口的目标 token 数。",
        "constraints": {"min": 1},
    },
    "window_l1.focus_max_tokens": {
        "label": "焦点最大 token",
        "description": "当前阅读焦点窗口允许的最大 token 数。",
        "constraints": {"min": 1},
    },
    "window_l1.min_focus_paragraphs": {
        "label": "最小焦点段落",
        "description": "焦点窗口至少保留的段落数。",
        "constraints": {"min": 1},
    },
    "window_l1.max_focus_paragraphs": {
        "label": "最大焦点段落",
        "description": "焦点窗口最多保留的段落数。",
        "constraints": {"min": 1},
    },
    "window_l1.overlap_paragraphs": {
        "label": "窗口重叠段落",
        "description": "相邻阅读窗口之间保留的重叠段落数。",
        "constraints": {"min": 0},
    },
    "window_l1.trigger_advance_ratio": {
        "label": "推进触发比例",
        "description": "阅读超过窗口比例后触发下一轮窗口推进。",
        "constraints": {"min": 0, "max": 1},
    },
    "window_l1.comment_density_soft_min": {
        "label": "评论软密度",
        "description": "用于提示评论 Agent 补足评论数量的软目标密度。",
        "constraints": {"min": 0, "max": 1},
    },
    "window_l1.comment_density_stat_window_paragraphs": {
        "label": "密度统计段落",
        "description": "计算近期评论密度时使用的段落窗口大小。",
        "constraints": {"min": 1},
    },
    "context.provider_context_limit_tokens": {
        "label": "供应商上下文上限",
        "description": "目标模型可接受的最大上下文 token 预算。",
        "constraints": {"min": 1},
    },
    "context.attention_target_input_tokens": {
        "label": "注意力目标输入",
        "description": "希望模型重点处理的输入 token 目标。",
        "constraints": {"min": 1},
    },
    "context.normal_target_input_tokens": {
        "label": "常规输入目标",
        "description": "正常请求构建上下文时的输入 token 目标。",
        "constraints": {"min": 1},
    },
    "context.compression_target_input_tokens": {
        "label": "压缩输入目标",
        "description": "压缩任务构建上下文时的输入 token 目标。",
        "constraints": {"min": 1},
    },
    "context.emergency_input_cap_tokens": {
        "label": "紧急输入上限",
        "description": "上下文退化时允许的硬输入上限。",
        "constraints": {"min": 1},
    },
    "context.reserved_tokens": {
        "label": "保留输出 token",
        "description": "为模型输出和工具调用预留的 token 预算。",
        "constraints": {"min": 0},
    },
    "context.target_chapter_summary_tokens": {
        "label": "章节摘要目标 token",
        "description": "压缩后章节摘要的目标长度。",
        "constraints": {"min": 1},
    },
    "context.max_chapter_summary_tokens": {
        "label": "章节摘要最大 token",
        "description": "压缩后章节摘要允许的最大长度。",
        "constraints": {"min": 1},
    },
    "context.max_anchor_excerpts": {
        "label": "最大锚点摘录数",
        "description": "压缩摘要中保留的关键原文锚点数量上限。",
        "constraints": {"min": 0},
    },
    "context.max_anchor_excerpt_tokens": {
        "label": "单条锚点 token",
        "description": "每条锚点摘录允许的最大 token 数。",
        "constraints": {"min": 1},
    },
    "context.max_context_jump_chars": {
        "label": "最大跳读字符",
        "description": "允许一次前向跳读补处理的最大字符数。",
        "constraints": {"min": 0},
    },
    "context.max_context_jump_tokens_estimate": {
        "label": "最大跳读 token",
        "description": "允许一次前向跳读补处理的最大估算 token 数。",
        "constraints": {"min": 0},
    },
    "context_l2.target_chunk_tokens": {
        "label": "Chunk 目标 token",
        "description": "L2 原文 chunk 的目标 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.min_chunk_tokens": {
        "label": "Chunk 最小 token",
        "description": "L2 原文 chunk 的最小 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.max_chunk_tokens": {
        "label": "Chunk 最大 token",
        "description": "L2 原文 chunk 的最大 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.max_chunk_chars": {
        "label": "Chunk 最大字符",
        "description": "L2 原文 chunk 的最大字符数。",
        "constraints": {"min": 1},
    },
    "context_l2.max_chunk_paragraphs": {
        "label": "Chunk 最大段落",
        "description": "L2 原文 chunk 的最大段落数。",
        "constraints": {"min": 1},
    },
    "context_l2.target_live_original_tokens": {
        "label": "活跃原文目标 token",
        "description": "未压缩活跃原文保留的目标 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.max_live_original_tokens": {
        "label": "活跃原文最大 token",
        "description": "未压缩活跃原文保留的最大 token 数。",
        "constraints": {"min": 1},
    },
    "context_l2.min_live_chunks_after_compaction": {
        "label": "压缩后最少活跃 chunk",
        "description": "每轮压缩后仍需保留的活跃原文 chunk 数。",
        "constraints": {"min": 0},
    },
    "context_l2.compaction_reclaim_chunk_count": {
        "label": "L2 回收 chunk 数",
        "description": "压缩时计划回收的已完成 chunk 数。",
        "constraints": {"min": 1},
    },
    "context_l3.preflight_trigger_input_tokens": {
        "label": "预检触发 token",
        "description": "进入压缩预检流程的输入 token 阈值。",
        "constraints": {"min": 1},
    },
    "context_l3.compression_trigger_input_tokens": {
        "label": "压缩触发 token",
        "description": "真正触发 L3 压缩任务的输入 token 阈值。",
        "constraints": {"min": 1},
    },
    "context_l3.max_completed_l2_chunks_before_compaction": {
        "label": "压缩前最大完成 chunk",
        "description": "超过该完成 chunk 数后优先触发压缩。",
        "constraints": {"min": 1},
    },
    "context_l3.min_completed_l2_chunks_before_compaction": {
        "label": "压缩前最小完成 chunk",
        "description": "未达到该完成 chunk 数时避免过早压缩。",
        "constraints": {"min": 1},
    },
    "context_l3.compaction_reclaim_chunk_count": {
        "label": "L3 回收 chunk 数",
        "description": "每次 L3 压缩尝试回收的 chunk 数。",
        "constraints": {"min": 1},
    },
    "context_l3.compaction_timeout_s": {
        "label": "压缩超时秒",
        "description": "单次压缩 Agent 调用的超时时间。",
        "constraints": {"min": 1},
    },
    "context_l3.allow_emergency_overflow_once": {
        "label": "允许一次紧急溢出",
        "description": "压缩未及时完成时是否允许一次临时超过目标预算。",
    },
    "ephemeral_comments.recent_focus_windows": {
        "label": "近期焦点窗口数",
        "description": "评论上下文中保留的近期焦点窗口数量。",
        "constraints": {"min": 0},
    },
    "ephemeral_comments.nearby_paragraph_margin": {
        "label": "附近段落边距",
        "description": "评论临时上下文纳入目标附近段落的范围。",
        "constraints": {"min": 0},
    },
    "ephemeral_comments.max_tokens": {
        "label": "评论临时 token",
        "description": "评论临时上下文的最大 token 预算。",
        "constraints": {"min": 0},
    },
    "ephemeral_comments.compress": {
        "label": "压缩评论临时上下文",
        "description": "是否压缩评论任务的临时上下文。",
    },
    "ephemeral_chat.recent_turns": {
        "label": "近期对话轮数",
        "description": "Chat 上下文中保留的最近完成对话轮数。",
        "constraints": {"min": 0},
    },
    "ephemeral_chat.max_tokens": {
        "label": "聊天临时 token",
        "description": "近期对话历史的最大 token 预算。",
        "constraints": {"min": 0},
    },
    "ephemeral_chat.compress": {
        "label": "压缩聊天临时上下文",
        "description": "是否压缩 Chat 临时上下文。",
    },
    "ephemeral_chat.scope": {
        "label": "聊天历史范围",
        "description": "选择 Chat 临时上下文引用的会话范围。",
        "type": "enum",
        "constraints": {"values": ["current_session"]},
    },
    "token_estimation.token_safety_margin": {
        "label": "Token 安全边际",
        "description": "本地估算值乘以该系数后作为安全估算。",
        "constraints": {"min": 1},
    },
    "token_estimation.calibration_percentile": {
        "label": "校准分位数",
        "description": "使用观测样本的该分位数作为校准依据。",
        "constraints": {"min": 0, "max": 1},
    },
    "token_estimation.calibration_window_size": {
        "label": "校准窗口大小",
        "description": "每个模型和 prompt 版本保留的校准样本数量。",
        "constraints": {"min": 1},
    },
    "token_estimation.min_calibration_samples": {
        "label": "最少校准样本",
        "description": "达到该样本数后才启用滚动校准。",
        "constraints": {"min": 1},
    },
    "token_estimation.default_bootstrap_calibration_ratio": {
        "label": "默认启动校准比",
        "description": "样本不足时使用的默认校准倍率。",
        "constraints": {"min": 0},
    },
    "observability.enabled": {
        "label": "启用可观测性",
        "description": "控制日志和遥测基础能力是否启用。",
    },
    "observability.provider": {
        "label": "可观测性提供方",
        "description": "当前可观测性后端类型。",
        "type": "enum",
        "constraints": {"values": ["otel"]},
    },
    "observability.log_json": {
        "label": "JSON 日志兼容开关",
        "description": "旧配置兼容项；实际输出格式以日志格式为准。",
    },
    "observability.log_format": {
        "label": "日志格式",
        "description": "控制控制台和文件日志的输出格式。",
        "type": "enum",
        "constraints": {"values": ["json", "text"]},
    },
    "observability.log_sinks": {
        "label": "日志输出",
        "description": "选择启用的日志 sink，例如 console、file、otel。",
        "type": "string_list",
    },
    "observability.log_level": {
        "label": "日志级别",
        "description": "后端日志最小输出级别。",
        "type": "enum",
        "constraints": {"values": ["DEBUG", "INFO", "WARNING", "ERROR"]},
    },
    "observability.environment": {
        "label": "运行环境",
        "description": "写入日志和遥测资源的环境名称。",
    },
    "observability.include_prompt_manifest": {
        "label": "审计 prompt 清单",
        "description": "旧配置兼容项；实际审计设置位于 observability.audit。",
    },
    "observability.include_full_prompt": {
        "label": "审计完整 prompt",
        "description": "旧配置兼容项；生产环境通常应关闭。",
    },
    "observability.service_name": {
        "label": "服务名",
        "description": "日志和遥测资源中的服务名称。",
    },
    "observability.otel_endpoint": {
        "label": "OTEL endpoint 兼容项",
        "description": "旧配置兼容字段；实际 endpoint 位于 observability.otel。",
        "constraints": {"format": "url_or_empty"},
    },
    "observability.console.enabled": {
        "label": "控制台日志",
        "description": "是否启用控制台日志输出。",
    },
    "observability.console.stream": {
        "label": "控制台流",
        "description": "控制台日志写入 stdout 或 stderr。",
        "type": "enum",
        "constraints": {"values": ["stdout", "stderr"]},
    },
    "observability.file.enabled": {
        "label": "文件日志",
        "description": "是否启用滚动文件日志。",
    },
    "observability.file.path": {
        "label": "日志文件路径",
        "description": "文件日志路径；相对路径按 data_dir 解析。",
    },
    "observability.file.max_bytes": {
        "label": "日志文件大小",
        "description": "单个日志文件滚动前的最大字节数。",
        "constraints": {"min": 1},
    },
    "observability.file.backup_count": {
        "label": "日志备份数量",
        "description": "滚动文件日志保留的备份文件数量。",
        "constraints": {"min": 0},
    },
    "observability.otel.enabled": {
        "label": "启用 OTEL",
        "description": "是否启用 OpenTelemetry 导出。",
    },
    "observability.otel.endpoint": {
        "label": "OTEL Endpoint",
        "description": "OTEL collector 的 HTTP base URL。",
        "constraints": {"format": "url_or_empty"},
    },
    "observability.otel.protocol": {
        "label": "OTEL 协议",
        "description": "遥测导出协议。",
        "type": "enum",
        "constraints": {"values": ["otlp_http"]},
    },
    "observability.otel.export_traces": {
        "label": "导出 traces",
        "description": "是否导出调用链追踪。",
    },
    "observability.otel.export_metrics": {
        "label": "导出 metrics",
        "description": "是否导出指标。",
    },
    "observability.otel.export_logs": {
        "label": "导出 logs",
        "description": "是否通过 OTEL 导出日志。",
    },
    "observability.otel.sample_ratio": {
        "label": "采样比例",
        "description": "trace 采样比例。",
        "constraints": {"min": 0, "max": 1},
    },
    "observability.audit.enabled": {
        "label": "启用 Agent 审计",
        "description": "是否将 Agent 运行摘要写入审计存储。",
    },
    "observability.audit.include_prompt_manifest": {
        "label": "包含 prompt 清单",
        "description": "审计包是否包含 prompt manifest。",
    },
    "observability.audit.include_full_prompt": {
        "label": "包含完整 prompt",
        "description": "审计包是否包含完整 prompt；可能包含正文，应谨慎开启。",
    },
    "observability.audit.include_model_response": {
        "label": "包含模型响应",
        "description": "审计包是否包含模型输出；默认关闭以降低泄露风险。",
    },
    "observability.audit.redact_secrets": {
        "label": "脱敏密钥",
        "description": "审计输出是否脱敏 api_key、authorization 等敏感字段。",
    },
}


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "string_list"
    return "string"


def _read_path(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


_MISSING_PATH = object()


def _read_mapping_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING_PATH
        current = current[part]
    return current


def _write_mapping_path(data: dict[str, Any], path: str, value: Any) -> None:
    current: dict[str, Any] = data
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _persisted_default_for_path(path: str) -> Any:
    return _read_path(Settings(), path)


def _preserve_env_override_paths(
    data: dict[str, Any],
    *,
    settings: Settings,
    raw: dict[str, Any],
    reset_env_override_paths: set[str],
) -> None:
    for path in settings.env_overrides:
        if path.split(".", 1)[0] not in data:
            continue
        if path in reset_env_override_paths:
            value = _persisted_default_for_path(path)
        else:
            value = _read_mapping_path(raw, path)
            if value is _MISSING_PATH:
                value = _persisted_default_for_path(path)
        _write_mapping_path(data, path, value)


def _dataclass_default(cls: type[Any], field_name: str) -> Any:
    for item in fields(cls):
        if item.name != field_name:
            continue
        if item.default is not MISSING:
            return item.default
        if item.default_factory is not MISSING:  # type: ignore[comparison-overlap]
            return item.default_factory()  # type: ignore[misc]
    return None


def _field_metadata(
    path: str,
    value: Any,
    default: Any,
    settings: Settings | None,
) -> dict[str, Any]:
    info = FIELD_INFO.get(path, {})
    metadata = {
        "path": path,
        "label": info.get("label", path.rsplit(".", 1)[-1]),
        "description": info.get("description", "该配置会影响后端运行行为。"),
        "type": info.get("type", _value_type(default)),
        "default": default,
        "constraints": info.get("constraints", {}),
    }
    if settings is not None and path in settings.env_overrides:
        metadata["env_override"] = {
            "env_var": settings.env_overrides[path],
            "effective_value": value,
        }
        metadata["read_only"] = True
    return metadata


def _collect_dataclass_fields(
    group_name: str,
    obj: Any,
    default_obj: Any,
    settings: Settings | None,
    prefix: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not is_dataclass(obj):
        return result
    for item in fields(obj):
        value = getattr(obj, item.name)
        default = getattr(default_obj, item.name, None)
        path = f"{group_name}.{prefix}{item.name}"
        if is_dataclass(value):
            nested_default = default if is_dataclass(default) else value.__class__()
            nested = _collect_dataclass_fields(
                group_name,
                value,
                nested_default,
                settings,
                prefix=f"{prefix}{item.name}.",
            )
            result.update(nested)
            continue
        result[path] = _field_metadata(path, value, default, settings)
    return result


def build_settings_metadata(settings: Settings | None = None) -> dict[str, Any]:
    current = settings or Settings()
    defaults = Settings()
    groups: dict[str, Any] = {}

    for group_name in (
        "models",
        "defaults",
        "active",
        "reader",
        "window_l1",
        "context",
        "context_l2",
        "context_l3",
        "ephemeral_comments",
        "ephemeral_chat",
        "token_estimation",
        "observability",
    ):
        label, description = GROUP_INFO[group_name]
        if group_name == "models":
            fields_meta = {
                key: _field_metadata(
                    key,
                    None,
                    _read_path(ModelConfig(), key.removeprefix("models[].")),
                    current,
                )
                for key in FIELD_INFO
                if key.startswith("models[].")
            }
        else:
            fields_meta = _collect_dataclass_fields(
                group_name,
                getattr(current, group_name),
                getattr(defaults, group_name),
                current,
            )
        groups[group_name] = {
            "label": label,
            "description": description,
            "fields": fields_meta,
        }

    groups["models"]["secret_policy"] = {
        "masked_value": MASKED_SECRET,
        "unchanged_sentinel": SECRET_UNCHANGED_SENTINEL,
        "readback": "api_key is never returned in plaintext",
    }
    groups["models"]["ignored_env"] = current.ignored_env.get("models", [])
    groups["models"]["read_only_env"] = current.read_only_env.get("llm", [])
    return {
        "groups": groups,
        "env_overrides": current.env_overrides,
        "ignored_env": current.ignored_env,
        "read_only_env": current.read_only_env,
        "migrations": current.migrations,
    }


def load_settings(*, write_migrations: bool = True) -> Settings:
    data_dir = pathlib.Path(_env("VIBE_READER_DATA_DIR") or str(_default_data_dir()))
    config_path = data_dir / "config.toml"

    raw: dict = {}
    if config_path.exists():
        raw = toml.load(config_path)

    env_overrides = _env_override_fields()
    llm_env = _env_values(LLM_ENV_KEYS)
    ignored_env: dict[str, list[str]] = {}
    read_only_env: dict[str, list[str]] = {}
    migrations: list[str] = []

    models = _parse_model_catalog(raw.get("models", []))
    legacy_llm_present = _has_legacy_llm(raw)
    if models:
        defaults, active = _load_model_refs(raw, models)
        if legacy_llm_present:
            migrations.append("legacy_llm_removed")
        if llm_env:
            ignored_env["models"] = sorted(llm_env)
        if legacy_llm_present and write_migrations:
            _clean_and_write_model_config(config_path, raw, models, defaults, active)
    elif legacy_llm_present:
        legacy_model = _legacy_model_from_raw(raw)
        models = [legacy_model]
        defaults = ModelDefaultsConfig(
            global_model_id=legacy_model.id,
            chat_model_id=legacy_model.id,
            comment_model_id=legacy_model.id,
        )
        active = ActiveModelsConfig()
        migrations.append("legacy_llm_migrated")
        if llm_env:
            ignored_env["models"] = sorted(llm_env)
        if write_migrations:
            _clean_and_write_model_config(config_path, raw, models, defaults, active)
    else:
        defaults = ModelDefaultsConfig()
        active = ActiveModelsConfig()
        if llm_env:
            read_only_env["llm"] = sorted(llm_env)

    if models:
        selected_model_id = _model_ref(
            active.global_model_id or defaults.global_model_id,
            {model.id: model for model in models},
            models[0].id,
        )
        selected_model = next(model for model in models if model.id == selected_model_id)
        llm = selected_model.to_llm()
    elif llm_env:
        llm = _env_llm_config()
    else:
        llm = LLMConfig()

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
        models=models,
        defaults=defaults,
        active=active,
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
        env_overrides=env_overrides,
        ignored_env=ignored_env,
        read_only_env=read_only_env,
        migrations=migrations,
    )
