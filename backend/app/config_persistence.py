from __future__ import annotations

import os
import pathlib
import tempfile
from dataclasses import asdict
from typing import Any

import toml

from .config_dataclasses import read_path
from .config_schema import (
    PERSISTED_SETTINGS_GROUPS,
    ActiveModelsConfig,
    ModelConfig,
    ModelDefaultsConfig,
    Settings,
)

_MISSING_PATH = object()


def read_raw_config(config_path: pathlib.Path) -> dict[str, Any]:
    if config_path.exists():
        return toml.load(config_path)
    return {}


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


def write_toml_user_only(path: pathlib.Path, data: dict[str, Any]) -> None:
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


def clean_and_write_model_config(
    config_path: pathlib.Path,
    raw: dict[str, Any],
    models: list[ModelConfig],
    defaults: ModelDefaultsConfig,
    active: ActiveModelsConfig,
) -> None:
    cleaned = dict(raw)
    cleaned.pop("llm", None)
    cleaned.update(_model_sections(models, defaults, active))
    write_toml_user_only(config_path, cleaned)


def settings_to_toml(settings: Settings) -> dict[str, Any]:
    data = _model_sections(settings.models, settings.defaults, settings.active)
    for group_name in PERSISTED_SETTINGS_GROUPS:
        data[group_name] = asdict(getattr(settings, group_name))
    return data


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
    return read_path(Settings(), path)


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


def save_settings(
    settings: Settings,
    path: pathlib.Path | None = None,
    *,
    reset_env_override_paths: set[str] | None = None,
) -> None:
    target_path = path or settings.config_path
    data = settings_to_toml(settings)
    raw = toml.load(target_path) if target_path.exists() else {}
    _preserve_env_override_paths(
        data,
        settings=settings,
        raw=raw,
        reset_env_override_paths=reset_env_override_paths or set(),
    )
    write_toml_user_only(target_path, data)
