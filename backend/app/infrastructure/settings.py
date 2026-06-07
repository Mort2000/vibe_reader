from __future__ import annotations

from threading import RLock
from typing import Any

from ..config_loader import load_settings
from ..config_schema import Settings


class SettingsProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = RLock()

    def current(self) -> Settings:
        with self._lock:
            return self._settings

    def replace(self, settings: Settings) -> Settings:
        with self._lock:
            self._settings = settings
            return self._settings


def current_settings(request: Any) -> Settings:
    provider = getattr(request.app.state, "settings_provider", None)
    if provider is not None:
        return provider.current()
    return request.app.state.settings


def apply_runtime_settings(request: Any, settings: Settings) -> Settings:
    request.app.state.settings = settings
    provider = getattr(request.app.state, "settings_provider", None)
    if provider is not None:
        provider.replace(settings)
    estimator = getattr(request.app.state, "token_estimator", None)
    if estimator is not None and hasattr(estimator, "replace_config"):
        estimator.replace_config(settings.token_estimation)

    from ..services.agent_base import prune_agent_caches

    prune_agent_caches(settings)
    return settings


def load_latest_settings(request: Any) -> Settings:
    return apply_runtime_settings(request, load_settings())
