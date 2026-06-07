from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass, replace
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Path as PathParam, Request

from ..config import (
    FIELD_INFO,
    PERSISTED_SETTINGS_GROUPS,
    THINK_EFFORT_VALUES,
    ActiveModelsConfig,
    ContextConfig,
    ContextL2Config,
    ContextL3Config,
    EphemeralChatConfig,
    EphemeralCommentsConfig,
    ModelConfig,
    ModelDefaultsConfig,
    ObservabilityConfig,
    ReaderConfig,
    Settings,
    TokenEstimationConfig,
    WindowL1Config,
    load_settings,
    merge_model_update,
    save_settings,
)
from ..errors import AppError
from ..services.llm_ping import ping_llm

router = APIRouter(tags=["config"])

DATACLASS_GROUPS: dict[str, type[Any]] = {
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
REF_GROUPS = {
    "defaults": ModelDefaultsConfig,
    "active": ActiveModelsConfig,
}
AGENT_ACTIVE_FIELDS = {
    "global": "global_model_id",
    "chat": "chat_model_id",
    "comment": "comment_model_id",
    "compaction": "comment_model_id",
}
COMMON_RESET_PRESETS = {
    "llm": ("models", "defaults", "active"),
    "reader_window": ("reader", "window_l1"),
    "context_budget": ("context", "context_l2", "context_l3"),
}
OBSERVABILITY_COMMON_PATHS = (
    "observability.log_level",
    "observability.log_sinks",
    "observability.otel.enabled",
    "observability.otel.endpoint",
    "observability.otel.export_traces",
    "observability.otel.export_metrics",
    "observability.otel.export_logs",
)


def current_settings(request: Request) -> Settings:
    provider = getattr(request.app.state, "settings_provider", None)
    if provider is not None:
        return provider.current()
    return request.app.state.settings


def apply_runtime_settings(request: Request, settings: Settings) -> Settings:
    request.app.state.settings = settings
    provider = getattr(request.app.state, "settings_provider", None)
    if provider is not None:
        provider.replace(settings)
    estimator = getattr(request.app.state, "token_estimator", None)
    if estimator is not None and hasattr(estimator, "replace_config"):
        estimator.replace_config(settings.token_estimation)
    return settings


def _load_latest_settings(request: Request) -> Settings:
    return apply_runtime_settings(request, load_settings())


def _field_error(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


def _raise_validation(errors: list[dict[str, str]]) -> None:
    if errors:
        errors = list({(item["path"], item["message"]): item for item in errors}.values())
        raise AppError(
            "validation_error",
            "配置校验失败",
            details={"fields": errors},
        )


def _reset_env_override_paths_from_payload(payload: dict[str, Any]) -> set[str]:
    raw_paths = payload.get("reset_env_override_paths", [])
    if raw_paths is None:
        return set()
    if not isinstance(raw_paths, list):
        raise AppError(
            "validation_error",
            "配置校验失败",
            details={
                "fields": [
                    _field_error(
                        "reset_env_override_paths",
                        "必须是配置项路径数组",
                    )
                ]
            },
        )
    return {str(path).strip() for path in raw_paths if str(path).strip()}


def _normalize_scope(scope: str) -> str:
    normalized = (scope or "global").strip().lower()
    if normalized not in AGENT_ACTIVE_FIELDS:
        raise AppError(
            "validation_error",
            "模型切换范围无效",
            details={
                "fields": [
                    _field_error("scope", "范围必须是 global、chat、comment 或 compaction")
                ]
            },
        )
    return normalized


def _effective_model_summary(settings: Settings, agent: str) -> dict[str, Any]:
    llm = settings.effective_llm(agent)
    model = settings.effective_model(agent)
    return {
        "agent": agent,
        "model_id": model.id if model is not None else llm.model_id,
        "provider": llm.provider,
        "model_name": llm.model,
        "think_effort": llm.think_effort,
        "source": llm.source,
        "base_url_configured": bool(llm.base_url),
        "api_key_configured": bool(llm.api_key),
    }


def effective_models_summary(settings: Settings) -> dict[str, Any]:
    return {
        "global": _effective_model_summary(settings, "global"),
        "chat": _effective_model_summary(settings, "chat"),
        "comment": _effective_model_summary(settings, "comment"),
        "compaction": _effective_model_summary(settings, "compaction"),
    }


def runtime_summary(settings: Settings) -> dict[str, Any]:
    global_llm = settings.effective_llm("global")
    return {
        "app": "vibe-reader-mini",
        "version": "0.1.0",
        "data_dir": str(settings.data_dir),
        "verify_mode": settings.verify_mode,
        "llm": {
            "base_url_configured": bool(global_llm.base_url),
            "api_key_configured": bool(global_llm.api_key),
            "model": global_llm.model,
            "model_name": global_llm.model,
            "provider": global_llm.provider,
            "source": global_llm.source,
        },
        "models": {
            "catalog_count": len(settings.models),
            "effective": effective_models_summary(settings),
        },
        "observability": {
            "enabled": settings.observability.enabled,
            "provider": settings.observability.provider,
        },
    }


def settings_summary(settings: Settings) -> dict[str, Any]:
    global_llm = settings.effective_llm("global")
    return {
        "models": settings.public_models(),
        "defaults": asdict(settings.defaults),
        "active": asdict(settings.active),
        "effective": effective_models_summary(settings),
        "llm": {
            "base_url_configured": bool(global_llm.base_url),
            "api_key_configured": bool(global_llm.api_key),
            "model": global_llm.model,
            "model_name": global_llm.model,
            "provider": global_llm.provider,
            "source": global_llm.source,
        },
        "reader": asdict(settings.reader),
        "context": {
            **asdict(settings.context),
            "effective_input_budget": settings.context.normal_target_input_tokens,
            "hard_input_cap": settings.context.emergency_input_cap_tokens,
        },
        "window_l1": {
            "lookahead_paragraphs": settings.reader.lookahead_paragraphs,
            **asdict(settings.window_l1),
        },
        "env": {
            "overrides": settings.env_overrides,
            "ignored": settings.ignored_env,
            "read_only": settings.read_only_env,
        },
    }


def _config_groups(settings: Settings) -> dict[str, Any]:
    return {
        group_name: asdict(getattr(settings, group_name))
        for group_name in PERSISTED_SETTINGS_GROUPS
    }


def _config_document(settings: Settings) -> dict[str, Any]:
    return {
        "config": {
            "models": settings.public_models(),
            "defaults": asdict(settings.defaults),
            "active": asdict(settings.active),
            "groups": _config_groups(settings),
        },
        "models": settings.public_models(),
        "defaults": asdict(settings.defaults),
        "active": asdict(settings.active),
        "effective": effective_models_summary(settings),
        "metadata": settings.ui_metadata(),
        "runtime": runtime_summary(settings),
        "policy": {
            "in_flight_model_switch": (
                "进行中的 Chat 流和 running 评论任务沿用启动时模型；新请求和新任务使用更新后的当前配置。"
            ),
            "compaction_model": "Context Compaction Agent 与 Comment Agent 共用模型。",
        },
    }


def _coerce_bool(value: Any, path: str, errors: list[dict[str, str]]) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    errors.append(_field_error(path, "必须是布尔值"))
    return False


def _coerce_scalar(
    value: Any,
    current: Any,
    path: str,
    errors: list[dict[str, str]],
) -> Any:
    if isinstance(current, bool):
        coerced = _coerce_bool(value, path, errors)
    elif isinstance(current, int) and not isinstance(current, bool):
        try:
            if isinstance(value, bool):
                raise TypeError
            coerced = int(value)
        except (TypeError, ValueError):
            errors.append(_field_error(path, "必须是整数"))
            return current
    elif isinstance(current, float):
        try:
            if isinstance(value, bool):
                raise TypeError
            coerced = float(value)
        except (TypeError, ValueError):
            errors.append(_field_error(path, "必须是数字"))
            return current
    elif isinstance(current, list):
        if isinstance(value, str):
            coerced = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, list | tuple):
            coerced = [str(item).strip() for item in value if str(item).strip()]
        else:
            errors.append(_field_error(path, "必须是字符串列表"))
            return current
    else:
        coerced = "" if value is None else str(value)
    _validate_constraints(path, coerced, errors)
    return coerced


def _validate_constraints(
    path: str,
    value: Any,
    errors: list[dict[str, str]],
) -> None:
    constraints = FIELD_INFO.get(path, {}).get("constraints", {})
    allowed = constraints.get("values")
    if allowed is not None and value not in allowed:
        errors.append(_field_error(path, f"必须是以下值之一：{', '.join(allowed)}"))
    fmt = constraints.get("format")
    if fmt == "url" and value and not _is_http_url(str(value)):
        errors.append(_field_error(path, "必须是有效的 http(s) URL"))
    if fmt == "url_or_empty" and value and not _is_http_url(str(value)):
        errors.append(_field_error(path, "必须为空或有效的 http(s) URL"))
    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = constraints.get("min")
        maximum = constraints.get("max")
        if minimum is not None and value < minimum:
            errors.append(_field_error(path, f"不能小于 {minimum}"))
        if maximum is not None and value > maximum:
            errors.append(_field_error(path, f"不能大于 {maximum}"))


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _coerce_dataclass_group(
    group_name: str,
    base: Any,
    payload: Any,
    errors: list[dict[str, str]],
    prefix: str = "",
) -> Any:
    if not isinstance(payload, dict):
        errors.append(_field_error(group_name, "配置分组必须是对象"))
        return base

    valid_names = {item.name for item in fields(base)}
    for key in payload:
        if key not in valid_names:
            errors.append(_field_error(f"{group_name}.{prefix}{key}", "未知配置项"))

    updates: dict[str, Any] = {}
    for item in fields(base):
        if item.name not in payload:
            continue
        current = getattr(base, item.name)
        path = f"{group_name}.{prefix}{item.name}"
        if is_dataclass(current):
            updates[item.name] = _coerce_dataclass_group(
                group_name,
                current,
                payload[item.name],
                errors,
                prefix=f"{prefix}{item.name}.",
            )
        else:
            updates[item.name] = _coerce_scalar(
                payload[item.name],
                current,
                path,
                errors,
            )
    return replace(base, **updates)


def _validate_model_payload(
    existing: ModelConfig | None,
    payload: Any,
    path: str,
    errors: list[dict[str, str]],
) -> ModelConfig:
    if not isinstance(payload, dict):
        errors.append(_field_error(path, "模型配置必须是对象"))
        return existing or ModelConfig()

    model = merge_model_update(existing, payload)
    if not model.id.strip():
        errors.append(_field_error(f"{path}.id", "模型 ID 不能为空"))
    if not model.model_name.strip():
        errors.append(_field_error(f"{path}.model_name", "模型名称不能为空"))
    if model.url.strip() and not _is_http_url(model.url):
        errors.append(_field_error(f"{path}.url", "必须是有效的 http(s) URL"))
    raw_effort = str(
        payload.get(
            "think_effort",
            existing.think_effort if existing is not None else "",
        )
        or ""
    ).strip()
    if raw_effort not in THINK_EFFORT_VALUES:
        errors.append(_field_error(f"{path}.think_effort", "思考力度取值无效"))
    return model


def _coerce_models(
    current_models: list[ModelConfig],
    payload: Any,
    errors: list[dict[str, str]],
) -> list[ModelConfig]:
    if not isinstance(payload, list):
        errors.append(_field_error("models", "模型目录必须是数组"))
        return current_models

    existing_by_id = {model.id: model for model in current_models}
    models: list[ModelConfig] = []
    seen: set[str] = set()
    for idx, item in enumerate(payload):
        item_id = str(item.get("id", "")).strip() if isinstance(item, dict) else ""
        model = _validate_model_payload(
            existing_by_id.get(item_id),
            item,
            f"models[{idx}]",
            errors,
        )
        if model.id in seen:
            errors.append(_field_error(f"models[{idx}].id", "模型 ID 必须唯一"))
        seen.add(model.id)
        models.append(model)
    return models


def _coerce_refs(
    group_name: str,
    base: ModelDefaultsConfig | ActiveModelsConfig,
    payload: Any,
    models: list[ModelConfig],
    errors: list[dict[str, str]],
) -> ModelDefaultsConfig | ActiveModelsConfig:
    if not isinstance(payload, dict):
        errors.append(_field_error(group_name, "模型引用配置必须是对象"))
        return base

    valid_names = {"global_model_id", "chat_model_id", "comment_model_id"}
    for key in payload:
        if key not in valid_names:
            errors.append(_field_error(f"{group_name}.{key}", "未知配置项"))

    catalog = {model.id for model in models}
    values = {
        name: str(payload.get(name, getattr(base, name)) or "").strip()
        for name in valid_names
    }
    for name, value in values.items():
        if value and value not in catalog:
            errors.append(_field_error(f"{group_name}.{name}", "引用的模型不存在"))

    if isinstance(base, ModelDefaultsConfig) and models:
        values["global_model_id"] = values["global_model_id"] or models[0].id
        values["chat_model_id"] = values["chat_model_id"] or values["global_model_id"]
        values["comment_model_id"] = (
            values["comment_model_id"] or values["global_model_id"]
        )
    cls = type(base)
    return cls(**values)


def _normalize_refs_for_models(
    defaults: ModelDefaultsConfig,
    active: ActiveModelsConfig,
    models: list[ModelConfig],
    errors: list[dict[str, str]],
) -> tuple[ModelDefaultsConfig, ActiveModelsConfig]:
    defaults = _coerce_refs("defaults", defaults, asdict(defaults), models, errors)
    active = _coerce_refs("active", active, asdict(active), models, errors)
    return defaults, active


def _settings_from_payload(
    current: Settings,
    payload: dict[str, Any],
) -> Settings:
    errors: list[dict[str, str]] = []
    data = payload.get("config", payload)
    if not isinstance(data, dict):
        raise AppError(
            "validation_error",
            "配置保存请求必须是对象",
            details={"fields": [_field_error("config", "必须是对象")]},
        )

    group_payloads = data.get("groups", {})
    if group_payloads is None:
        group_payloads = {}
    if not isinstance(group_payloads, dict):
        errors.append(_field_error("groups", "配置分组必须是对象"))
        group_payloads = {}

    models = current.models
    if "models" in data:
        models = _coerce_models(current.models, data["models"], errors)

    defaults = current.defaults
    active = current.active
    if "defaults" in data:
        defaults = _coerce_refs("defaults", defaults, data["defaults"], models, errors)
    if "active" in data:
        active = _coerce_refs("active", active, data["active"], models, errors)
    defaults, active = _normalize_refs_for_models(defaults, active, models, errors)

    updates: dict[str, Any] = {
        "models": models,
        "defaults": defaults,
        "active": active,
    }
    for group_name in PERSISTED_SETTINGS_GROUPS:
        incoming = data.get(group_name, group_payloads.get(group_name))
        if incoming is None:
            continue
        updates[group_name] = _coerce_dataclass_group(
            group_name,
            getattr(current, group_name),
            incoming,
            errors,
        )

    _raise_validation(errors)
    return replace(current, **updates)


def _save_and_reload(
    request: Request,
    settings: Settings,
    *,
    reset_env_override_paths: set[str] | None = None,
) -> Settings:
    save_settings(settings, reset_env_override_paths=reset_env_override_paths)
    return _load_latest_settings(request)


def _referencing_paths(settings: Settings, model_id: str) -> list[str]:
    paths: list[str] = []
    for group_name in ("defaults", "active"):
        refs = getattr(settings, group_name)
        for field_name in ("global_model_id", "chat_model_id", "comment_model_id"):
            if getattr(refs, field_name) == model_id:
                paths.append(f"{group_name}.{field_name}")
    return paths


def _replace_path_value(obj: Any, parts: list[str], value: Any) -> Any:
    if not parts:
        return value
    head, *tail = parts
    child = getattr(obj, head)
    return replace(obj, **{head: _replace_path_value(child, tail, value)})


def _reset_field(settings: Settings, path: str) -> Settings:
    if path.startswith("defaults."):
        default_refs = ModelDefaultsConfig()
        field_name = path.split(".", 1)[1]
        if field_name not in {"global_model_id", "chat_model_id", "comment_model_id"}:
            _raise_validation([_field_error("path", "不支持重置该配置项")])
        value = getattr(default_refs, field_name)
        return replace(
            settings,
            defaults=replace(settings.defaults, **{field_name: value}),
        )
    if path.startswith("active."):
        active_refs = ActiveModelsConfig()
        field_name = path.split(".", 1)[1]
        if field_name not in {"global_model_id", "chat_model_id", "comment_model_id"}:
            _raise_validation([_field_error("path", "不支持重置该配置项")])
        value = getattr(active_refs, field_name)
        return replace(
            settings,
            active=replace(settings.active, **{field_name: value}),
        )

    group_name, _, field_path = path.partition(".")
    if group_name not in DATACLASS_GROUPS or not field_path:
        raise AppError(
            "validation_error",
            "配置项路径无效",
            details={"fields": [_field_error("path", "不支持重置该配置项")]},
        )
    group = getattr(settings, group_name)
    default_group = DATACLASS_GROUPS[group_name]()
    value = _read_nested_attr(default_group, field_path.split("."), path)
    new_group = _replace_path_value(group, field_path.split("."), value)
    return replace(settings, **{group_name: new_group})


def _read_nested_attr(obj: Any, parts: list[str], path: str) -> Any:
    current = obj
    for part in parts:
        if not hasattr(current, part):
            _raise_validation([_field_error("path", f"不支持重置 {path}")])
        current = getattr(current, part)
    return current


def _reset_group(settings: Settings, group_name: str) -> Settings:
    if group_name == "models":
        return replace(
            settings,
            models=[],
            defaults=ModelDefaultsConfig(),
            active=ActiveModelsConfig(),
        )
    if group_name == "defaults":
        defaults, active = _normalize_refs_for_models(
            ModelDefaultsConfig(),
            settings.active,
            settings.models,
            [],
        )
        return replace(settings, defaults=defaults, active=active)
    if group_name == "active":
        return replace(settings, active=ActiveModelsConfig())
    if group_name in DATACLASS_GROUPS:
        return replace(settings, **{group_name: DATACLASS_GROUPS[group_name]()})
    raise AppError(
        "validation_error",
        "配置分组无效",
        details={"fields": [_field_error("group", "不支持重置该配置分组")]},
    )


def _field_paths_for_group(settings: Settings, group_name: str) -> set[str]:
    group_meta = settings.ui_metadata().get("groups", {}).get(group_name, {})
    return set(group_meta.get("fields", {}))


def _reset_preset(
    settings: Settings,
    preset: str,
) -> tuple[Settings, set[str]]:
    if preset == "observability_common":
        reset_paths = set(OBSERVABILITY_COMMON_PATHS)
        for path in OBSERVABILITY_COMMON_PATHS:
            settings = _reset_field(settings, path)
        return settings, reset_paths

    group_names = COMMON_RESET_PRESETS.get(preset)
    if group_names is None:
        raise AppError(
            "validation_error",
            "常用重置范围无效",
            details={"fields": [_field_error("preset", "不支持该常用重置范围")]},
        )
    reset_paths: set[str] = set()
    for group_name in group_names:
        reset_paths.update(_field_paths_for_group(settings, group_name))
        settings = _reset_group(settings, group_name)
    return settings, reset_paths


@router.get("/config")
async def read_config(request: Request) -> dict[str, Any]:
    return _config_document(_load_latest_settings(request))


@router.get("/config/schema")
async def read_config_schema(request: Request) -> dict[str, Any]:
    return current_settings(request).ui_metadata()


@router.put("/config")
async def save_config(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    settings = _settings_from_payload(current_settings(request), body)
    saved = _save_and_reload(
        request,
        settings,
        reset_env_override_paths=_reset_env_override_paths_from_payload(body),
    )
    return _config_document(saved)


@router.post("/config/reset")
async def reset_config(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    settings = current_settings(request)
    scope = str(body.get("scope") or "").strip()
    reset_paths: set[str] = set()

    if scope == "field":
        path = str(body.get("path") or "").strip()
        if not path:
            raise AppError(
                "validation_error",
                "缺少配置项路径",
                details={"fields": [_field_error("path", "必须提供 path")]},
            )
        settings = _reset_field(settings, path)
        reset_paths.add(path)
    elif scope == "group":
        group_name = str(body.get("group") or "").strip()
        reset_paths.update(_field_paths_for_group(settings, group_name))
        settings = _reset_group(settings, group_name)
    elif scope in {"preset", "common"}:
        settings, reset_paths = _reset_preset(
            settings,
            str(body.get("preset") or "").strip(),
        )
    else:
        raise AppError(
            "validation_error",
            "重置范围无效",
            details={"fields": [_field_error("scope", "必须是 field、group 或 preset")]},
        )

    saved = _save_and_reload(
        request,
        settings,
        reset_env_override_paths=reset_paths & set(settings.env_overrides),
    )
    return _config_document(saved)


@router.post("/config/models")
async def create_model(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    settings = current_settings(request)
    errors: list[dict[str, str]] = []
    model = _validate_model_payload(None, body, "model", errors)
    if model.id in settings.model_catalog:
        errors.append(_field_error("model.id", "模型 ID 已存在"))
    _raise_validation(errors)

    models = [*settings.models, model]
    defaults, active = _normalize_refs_for_models(
        settings.defaults,
        settings.active,
        models,
        [],
    )
    saved = _save_and_reload(
        request,
        replace(settings, models=models, defaults=defaults, active=active),
    )
    return _config_document(saved)


@router.put("/config/models/{model_id}")
async def update_model(
    request: Request,
    body: dict[str, Any],
    model_id: str = PathParam(...),
) -> dict[str, Any]:
    settings = current_settings(request)
    existing = settings.model_catalog.get(model_id)
    if existing is None:
        raise AppError(
            "validation_error",
            "模型不存在",
            details={"fields": [_field_error("model_id", "模型不存在")]},
        )
    requested_id = str(body.get("id", model_id) or "").strip()
    if requested_id != model_id:
        raise AppError(
            "validation_error",
            "不能通过编辑接口修改模型 ID",
            details={"fields": [_field_error("id", "模型 ID 不可修改")]},
        )

    errors: list[dict[str, str]] = []
    model = _validate_model_payload(existing, {**body, "id": model_id}, "model", errors)
    _raise_validation(errors)
    models = [model if item.id == model_id else item for item in settings.models]
    saved = _save_and_reload(request, replace(settings, models=models))
    return _config_document(saved)


@router.delete("/config/models/{model_id}")
async def delete_model(
    request: Request,
    model_id: str = PathParam(...),
) -> dict[str, Any]:
    settings = current_settings(request)
    if model_id not in settings.model_catalog:
        raise AppError(
            "validation_error",
            "模型不存在",
            details={"fields": [_field_error("model_id", "模型不存在")]},
        )
    refs = _referencing_paths(settings, model_id)
    if refs:
        raise AppError(
            "config_reference_conflict",
            "模型正在被默认或当前选择引用，不能删除",
            details={"fields": [_field_error(path, "请先切换到其他模型") for path in refs]},
        )

    models = [model for model in settings.models if model.id != model_id]
    defaults, active = _normalize_refs_for_models(
        settings.defaults,
        settings.active,
        models,
        [],
    )
    saved = _save_and_reload(
        request,
        replace(settings, models=models, defaults=defaults, active=active),
    )
    return _config_document(saved)


@router.post("/config/models/ping")
async def ping_model(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = current_settings(request)
    payload = body or {}
    model_id = str(payload.get("model_id") or "").strip()

    if model_id:
        existing = settings.model_catalog.get(model_id)
        if existing is None:
            raise AppError(
                "validation_error",
                "模型不存在",
                details={"fields": [_field_error("model_id", "模型不存在")]},
            )
        model_payload = payload.get("model")
        if isinstance(model_payload, dict):
            errors: list[dict[str, str]] = []
            model = _validate_model_payload(existing, model_payload, "model", errors)
            _raise_validation(errors)
        else:
            model = existing
    else:
        model_payload = payload.get("model", payload)
        errors = []
        model = _validate_model_payload(None, model_payload, "model", errors)
        _raise_validation(errors)

    result = await ping_llm(model)
    result["model"] = result.get("model") or model.model_name
    result["model_id"] = model.id
    return result


@router.post("/config/active")
async def switch_active_model(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    settings = current_settings(request)
    scope = _normalize_scope(str(body.get("scope") or "global"))
    model_id = str(body.get("model_id") or "").strip()
    if model_id and model_id not in settings.model_catalog:
        raise AppError(
            "validation_error",
            "引用的模型不存在",
            details={"fields": [_field_error("model_id", "引用的模型不存在")]},
        )

    field_name = AGENT_ACTIVE_FIELDS[scope]
    active = replace(settings.active, **{field_name: model_id})
    saved = _save_and_reload(request, replace(settings, active=active))
    return _config_document(saved)
