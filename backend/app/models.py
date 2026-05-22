from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Request / Response models ---


class ProgressUpdateRequest:
    __slots__ = ("chapter_idx", "paragraph_idx", "scroll_pct")

    def __init__(self, chapter_idx: int, paragraph_idx: int, scroll_pct: float):
        self.chapter_idx = chapter_idx
        self.paragraph_idx = paragraph_idx
        self.scroll_pct = scroll_pct
