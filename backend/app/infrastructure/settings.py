from __future__ import annotations

from ..config import Settings


class SettingsProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def current(self) -> Settings:
        return self._settings
