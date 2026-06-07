from __future__ import annotations

import os
import pathlib
from dataclasses import replace
from typing import Any

from .config_dataclasses import coerce_dataclass_group
from .config_persistence import clean_and_write_model_config, read_raw_config
from .config_schema import (
    LLM_ENV_KEYS,
    NON_LLM_ENV_FIELD_PATHS,
    PERSISTED_SETTINGS_GROUP_TYPES,
    ActiveModelsConfig,
    LLMConfig,
    ModelConfig,
    ModelDefaultsConfig,
    ObservabilityConfig,
    ObservabilityFileConfig,
    Settings,
    _coerce_model_id,
    _coerce_provider,
    _coerce_think_effort,
    _default_data_dir,
)


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


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _legacy_model_from_raw(raw: dict[str, Any]) -> ModelConfig:
    llm_raw = _mapping(raw.get("llm"))
    default_model = ModelConfig()
    return ModelConfig(
        id=_coerce_model_id(llm_raw.get("id"), default_model.id),
        provider=_coerce_provider(llm_raw.get("provider")),
        url=str(llm_raw.get("url", llm_raw.get("base_url", default_model.url)) or ""),
        model_name=str(
            llm_raw.get("model_name", llm_raw.get("model", default_model.model_name))
            or default_model.model_name
        ),
        api_key=str(llm_raw.get("api_key", default_model.api_key) or ""),
        think_effort=_coerce_think_effort(llm_raw.get("think_effort")),
    )


def _env_llm_config() -> LLMConfig:
    default_llm = LLMConfig()
    return LLMConfig(
        base_url=_env("VIBE_READER_LLM_BASE_URL") or "",
        api_key=_env("VIBE_READER_LLM_API_KEY") or "",
        model=_env("VIBE_READER_LLM_MODEL") or default_llm.model,
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
        default_model = ModelConfig(id=model_id)
        models.append(
            ModelConfig(
                id=model_id,
                provider=_coerce_provider(item.get("provider")),
                url=str(item.get("url", item.get("base_url", default_model.url)) or ""),
                model_name=str(
                    item.get("model_name", item.get("model", default_model.model_name))
                    or default_model.model_name
                ),
                api_key=str(item.get("api_key", default_model.api_key) or ""),
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
    defaults_raw = _mapping(raw.get("defaults"))
    active_raw = _mapping(raw.get("active"))

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
    active_defaults = ActiveModelsConfig()
    active = ActiveModelsConfig(
        global_model_id=_model_ref(
            active_raw.get("global_model_id"), catalog, active_defaults.global_model_id
        ),
        chat_model_id=_model_ref(
            active_raw.get("chat_model_id"), catalog, active_defaults.chat_model_id
        ),
        comment_model_id=_model_ref(
            active_raw.get("comment_model_id"),
            catalog,
            active_defaults.comment_model_id,
        ),
    )
    return defaults, active


def _load_model_catalog_state(
    config_path: pathlib.Path,
    raw: dict[str, Any],
    llm_env: dict[str, str],
    *,
    write_migrations: bool,
) -> tuple[
    list[ModelConfig],
    ModelDefaultsConfig,
    ActiveModelsConfig,
    dict[str, list[str]],
    dict[str, list[str]],
    list[str],
]:
    ignored_env: dict[str, list[str]] = {}
    read_only_env: dict[str, list[str]] = {}
    migrations: list[str] = []
    models = _parse_model_catalog(raw.get("models"))
    legacy_llm_present = _has_legacy_llm(raw)

    if models:
        defaults, active = _load_model_refs(raw, models)
        if legacy_llm_present:
            migrations.append("legacy_llm_removed")
        if llm_env:
            ignored_env["models"] = sorted(llm_env)
        if legacy_llm_present and write_migrations:
            clean_and_write_model_config(config_path, raw, models, defaults, active)
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
            clean_and_write_model_config(config_path, raw, models, defaults, active)
    else:
        defaults = ModelDefaultsConfig()
        active = ActiveModelsConfig()
        if llm_env:
            read_only_env["llm"] = sorted(llm_env)

    return models, defaults, active, ignored_env, read_only_env, migrations


def _global_llm_config(
    models: list[ModelConfig],
    defaults: ModelDefaultsConfig,
    active: ActiveModelsConfig,
    llm_env: dict[str, str],
) -> LLMConfig:
    if models:
        selected_model_id = _model_ref(
            active.global_model_id or defaults.global_model_id,
            {model.id: model for model in models},
            models[0].id,
        )
        selected_model = next(model for model in models if model.id == selected_model_id)
        return selected_model.to_llm()
    if llm_env:
        return _env_llm_config()
    return LLMConfig()


def _load_dataclass_groups(raw: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for group_name, group_type in PERSISTED_SETTINGS_GROUP_TYPES.items():
        if group_name == "observability":
            continue
        groups[group_name] = coerce_dataclass_group(
            group_name,
            group_type(),
            raw.get(group_name),
            strict=False,
            reject_unknown=False,
        )
    groups["observability"] = _load_observability(raw)
    return groups


def _load_observability(raw: dict[str, Any]) -> ObservabilityConfig:
    obs_raw = _mapping(raw.get("observability"))
    obs = coerce_dataclass_group(
        "observability",
        ObservabilityConfig(),
        obs_raw,
        strict=False,
        reject_unknown=False,
    )
    obs_otel_raw = _mapping(obs_raw.get("otel"))
    obs_audit_raw = _mapping(obs_raw.get("audit"))
    obs_console_raw = _mapping(obs_raw.get("console"))
    obs_file_raw = _mapping(obs_raw.get("file"))

    defaults = ObservabilityConfig()
    log_json_default = _as_bool(obs_raw.get("log_json"), defaults.log_json)
    log_format = _normalized_log_format(
        _override_env("VIBE_READER_LOG_FORMAT", obs_raw.get("log_format")),
        default_json=log_json_default,
    )
    log_sinks = _as_str_list(
        _override_env("VIBE_READER_LOG_SINKS", obs_raw.get("log_sinks")),
        defaults.log_sinks,
    )
    otel_endpoint = _override_env(
        "VIBE_READER_OTEL_ENDPOINT",
        _first_not_none(
            obs_otel_raw.get("endpoint"),
            obs_raw.get("endpoint"),
            defaults.otel.endpoint,
        ),
    )
    otel = replace(
        obs.otel,
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
        protocol=obs_otel_raw.get("protocol", defaults.otel.protocol),
        export_traces=_as_bool(
            _override_env(
                "VIBE_READER_OTEL_EXPORT_TRACES",
                obs_otel_raw.get("export_traces"),
            ),
            defaults.otel.export_traces,
        ),
        export_metrics=_as_bool(
            _override_env(
                "VIBE_READER_OTEL_EXPORT_METRICS",
                obs_otel_raw.get("export_metrics"),
            ),
            defaults.otel.export_metrics,
        ),
        export_logs=_as_bool(
            _override_env(
                "VIBE_READER_OTEL_EXPORT_LOGS",
                obs_otel_raw.get("export_logs"),
            ),
            defaults.otel.export_logs,
        ),
        sample_ratio=_as_float(
            _override_env(
                "VIBE_READER_OTEL_SAMPLE_RATIO",
                obs_otel_raw.get("sample_ratio"),
            ),
            defaults.otel.sample_ratio,
        ),
    )
    audit = replace(
        obs.audit,
        enabled=_as_bool(obs_audit_raw.get("enabled"), defaults.audit.enabled),
        include_prompt_manifest=_as_bool(
            obs_audit_raw.get(
                "include_prompt_manifest",
                obs_raw.get("include_prompt_manifest"),
            ),
            defaults.audit.include_prompt_manifest,
        ),
        include_full_prompt=_as_bool(
            obs_audit_raw.get("include_full_prompt", obs_raw.get("include_full_prompt")),
            defaults.audit.include_full_prompt,
        ),
        include_model_response=_as_bool(
            obs_audit_raw.get("include_model_response"),
            defaults.audit.include_model_response,
        ),
        redact_secrets=_as_bool(
            obs_audit_raw.get("redact_secrets"),
            defaults.audit.redact_secrets,
        ),
    )
    file_defaults = ObservabilityFileConfig(enabled="file" in log_sinks)
    file = replace(
        obs.file,
        enabled=_as_bool(obs_file_raw.get("enabled"), file_defaults.enabled),
        path=obs_file_raw.get("path", defaults.file.path),
        max_bytes=_as_int(obs_file_raw.get("max_bytes"), defaults.file.max_bytes),
        backup_count=_as_int(obs_file_raw.get("backup_count"), defaults.file.backup_count),
    )
    return replace(
        obs,
        enabled=_as_bool(
            _override_env("VIBE_READER_OBSERVABILITY_ENABLED", obs_raw.get("enabled")),
            defaults.enabled,
        ),
        provider=obs_raw.get("provider", defaults.provider),
        log_json=log_format == "json",
        log_format=log_format,
        log_sinks=log_sinks,
        log_level=_override_env(
            "VIBE_READER_LOG_LEVEL",
            obs_raw.get("log_level", defaults.log_level),
        ),
        environment=_override_env(
            "VIBE_READER_ENVIRONMENT",
            obs_raw.get("environment", defaults.environment),
        ),
        include_prompt_manifest=audit.include_prompt_manifest,
        include_full_prompt=audit.include_full_prompt,
        service_name=obs_raw.get("service_name", defaults.service_name)
        if isinstance(obs_raw.get("service_name", defaults.service_name), str)
        else defaults.service_name,
        otel_endpoint=otel.endpoint,
        console=replace(
            obs.console,
            enabled=_as_bool(obs_console_raw.get("enabled"), defaults.console.enabled),
            stream=obs_console_raw.get("stream", defaults.console.stream),
        ),
        file=file,
        otel=otel,
        audit=audit,
    )


def load_settings(*, write_migrations: bool = True) -> Settings:
    data_dir = pathlib.Path(_env("VIBE_READER_DATA_DIR") or str(_default_data_dir()))
    config_path = data_dir / "config.toml"
    raw = read_raw_config(config_path)

    env_overrides = _env_override_fields()
    llm_env = _env_values(LLM_ENV_KEYS)
    models, defaults, active, ignored_env, read_only_env, migrations = (
        _load_model_catalog_state(
            config_path,
            raw,
            llm_env,
            write_migrations=write_migrations,
        )
    )
    llm = _global_llm_config(models, defaults, active, llm_env)
    groups = _load_dataclass_groups(raw)
    verify_mode = _as_bool(_env("VIBE_READER_VERIFY_MODE"), False)

    return Settings(
        data_dir=data_dir,
        llm=llm,
        models=models,
        defaults=defaults,
        active=active,
        verify_mode=verify_mode,
        env_overrides=env_overrides,
        ignored_env=ignored_env,
        read_only_env=read_only_env,
        migrations=migrations,
        **groups,
    )
