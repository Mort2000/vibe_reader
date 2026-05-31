"""Parse ``comment_target_paragraphs`` range notation from prompt text.

Must stay aligned with ``llm_stub/aimock/server.mjs`` ``parseTargetParagraphs``.
Product source of truth: ``app.services.context_builder._paragraphs_to_ranges``.
"""

from __future__ import annotations

import re

_COMMENT_TARGETS_RE = re.compile(
    r"comment_target_paragraphs\s*=\s*\[([^\]]+)\]",
    re.MULTILINE,
)
_RANGE_RE = re.compile(r"^(\d+)\.\.=(\d+)$")
_SINGLE_RE = re.compile(r"^(\d+)$")


def expand_comment_target_spec(inner: str) -> list[int]:
    """Expand bracket contents to sorted unique paragraph indices."""
    if not inner.strip():
        return []
    out: list[int] = []
    seen: set[int] = set()
    for part in inner.split(","):
        token = part.strip()
        if not token:
            continue
        m_range = _RANGE_RE.match(token)
        if m_range:
            start, end = int(m_range.group(1)), int(m_range.group(2))
            if start > end:
                start, end = end, start
            for p in range(start, end + 1):
                if p not in seen:
                    seen.add(p)
                    out.append(p)
            continue
        m_single = _SINGLE_RE.match(token)
        if m_single:
            p = int(m_single.group(1))
            if p not in seen:
                seen.add(p)
                out.append(p)
    out.sort()
    return out


def parse_comment_target_paragraphs(content: str) -> list[int]:
    """Parse all ``comment_target_paragraphs = [...]`` assignments in *content*."""
    match = _COMMENT_TARGETS_RE.search(content)
    if not match:
        return []
    return expand_comment_target_spec(match.group(1))
