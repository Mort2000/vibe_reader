from __future__ import annotations

from threading import RLock

from ..config import Settings


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
