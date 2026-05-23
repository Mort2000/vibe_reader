"""V-15: Audit sample exporter — comment samples for subjective review."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import VerifyConfig
from .run import RunManager

MAX_EXCERPT_CHARS = 400


@dataclass
class CommentSampleDraft:
    scenario_id: str
    book: dict[str, Any]
    chapter_idx: int
    paragraph_idx: int
    comment: dict[str, Any]
    source_paragraph: str = ""
    neighbor_paragraphs: list[dict[str, Any]] = field(default_factory=list)
    window: dict[str, Any] | None = None
    model: str = ""
    trace_id: str = ""
    prompt_version: str = ""
    context_hash: str = ""
    tokens: dict[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None
    rolling_snapshot_excerpt: str = ""
    recent_comment_digest: str = ""


class CommentAuditExporter:
    """Collects and writes comment audit samples to the run output directory."""

    def __init__(self, run_manager: RunManager, config: VerifyConfig):
        self.run_manager = run_manager
        self.config = config
        self._drafts: list[tuple[str, CommentSampleDraft]] = []
        self._counter = 0

    @property
    def sample_count(self) -> int:
        return len(self._drafts)

    def add_comment(self, draft: CommentSampleDraft) -> str:
        self._counter += 1
        sample_id = f"comment_{draft.scenario_id}_{self._counter:04d}"
        self._drafts.append((sample_id, draft))
        return sample_id

    def add_comments_from_window(
        self,
        comments: list[dict[str, Any]],
        *,
        scenario_id: str,
        book: dict[str, Any],
        chapter_idx: int,
        window: dict[str, Any] | None,
        paragraphs: list[dict[str, Any]],
        model: str = "",
        latency_by_trace: dict[str, float] | None = None,
        tokens_by_trace: dict[str, dict[str, Any]] | None = None,
        trace_meta_by_trace_id: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]:
        """Sample up to ``sample_comments_per_window`` comments from one window."""
        if not comments:
            return []

        per_window = self.config.audit.sample_comments_per_window
        window_id = window.get("id") if window else None
        grouped = [c for c in comments if window_id is None or c.get("window_id") == window_id]
        if not grouped:
            grouped = comments

        by_para: dict[int, dict[str, Any]] = {}
        for comment in grouped:
            pidx = comment.get("paragraph_idx")
            if pidx is not None and pidx not in by_para:
                by_para[pidx] = comment

        selected = sorted(by_para.values(), key=lambda c: c.get("paragraph_idx", 0))[
            :per_window
        ]

        text_map = {p["paragraph_idx"]: p.get("text", "") for p in paragraphs}
        sample_ids: list[str] = []
        for comment in selected:
            pidx = comment["paragraph_idx"]
            trace_id = comment.get("trace_id") or ""
            trace_meta = (trace_meta_by_trace_id or {}).get(trace_id, {})
            # TODO(V-15): populate rolling_snapshot_excerpt and recent_comment_digest
            # once window/snapshot APIs expose audit-safe excerpts for verification.
            draft = CommentSampleDraft(
                scenario_id=scenario_id,
                book=book,
                chapter_idx=chapter_idx,
                paragraph_idx=pidx,
                comment=comment,
                source_paragraph=_excerpt(
                    text_map.get(pidx, ""),
                    self.config.audit.include_original_excerpts,
                ),
                neighbor_paragraphs=_neighbor_excerpts(
                    text_map,
                    pidx,
                    self.config.audit.include_original_excerpts,
                ),
                window=_window_payload(window),
                model=model,
                trace_id=trace_id,
                prompt_version=trace_meta.get("prompt_version", ""),
                context_hash=trace_meta.get("context_hash", ""),
                latency_ms=(latency_by_trace or {}).get(trace_id),
                tokens=(tokens_by_trace or {}).get(trace_id, {}),
            )
            sample_ids.append(self.add_comment(draft))
        return sample_ids

    def export(self) -> tuple[int, int]:
        """Write ``audit/comments.ndjson`` and markdown samples."""
        if not self._drafts:
            return 0, 0

        records = [self._to_record(sample_id, draft) for sample_id, draft in self._drafts]
        audit_dir = self.run_manager.base_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        samples_dir = audit_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        ndjson_path = audit_dir / "comments.ndjson"
        with open(ndjson_path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        md_count = 0
        for record in records:
            md_path = samples_dir / f"{record['sample_id']}.md"
            md_path.write_text(_render_markdown(record), encoding="utf-8")
            md_count += 1

        return len(records), md_count

    def _to_record(self, sample_id: str, draft: CommentSampleDraft) -> dict[str, Any]:
        comment = draft.comment
        return {
            "sample_id": sample_id,
            "run_id": self.run_manager.run_id,
            "scenario_id": draft.scenario_id,
            "book": {
                "id": draft.book.get("id"),
                "title": draft.book.get("title"),
            },
            "chapter_idx": draft.chapter_idx,
            "paragraph_idx": draft.paragraph_idx,
            "window": draft.window,
            "source_paragraph": draft.source_paragraph,
            "neighbor_paragraphs": draft.neighbor_paragraphs,
            "rolling_snapshot_excerpt": draft.rolling_snapshot_excerpt,
            "recent_comment_digest": draft.recent_comment_digest,
            "ai_comment": comment.get("comment", ""),
            "comment_type": comment.get("comment_type"),
            "model": draft.model,
            "trace_id": draft.trace_id,
            "prompt_version": draft.prompt_version,
            "context_hash": draft.context_hash,
            "tokens": draft.tokens,
            "latency_ms": draft.latency_ms,
            "created_at": comment.get("created_at")
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def _excerpt(text: str, include: bool) -> str:
    if not include:
        return ""
    text = text.strip()
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    return text[:MAX_EXCERPT_CHARS] + "…"


def _neighbor_excerpts(
    text_map: dict[int, str],
    paragraph_idx: int,
    include: bool,
) -> list[dict[str, Any]]:
    if not include:
        return []
    neighbors: list[dict[str, Any]] = []
    for offset in (-1, 1):
        idx = paragraph_idx + offset
        if idx in text_map:
            neighbors.append(
                {"paragraph_idx": idx, "text": _excerpt(text_map[idx], True)}
            )
    return neighbors


def _window_payload(window: dict[str, Any] | None) -> dict[str, Any] | None:
    if not window:
        return None
    return {
        "id": window.get("id"),
        "seq": window.get("window_seq", window.get("seq")),
        "start_paragraph_idx": window.get("start_paragraph_idx"),
        "end_paragraph_idx": window.get("end_paragraph_idx"),
        "focus_start_paragraph_idx": window.get("focus_start_paragraph_idx"),
        "focus_end_paragraph_idx": window.get("focus_end_paragraph_idx"),
        "status": window.get("status"),
    }


def _render_markdown(record: dict[str, Any]) -> str:
    lines = [
        f"# Comment Sample `{record['sample_id']}`",
        "",
        f"- **Scenario**: {record['scenario_id']}",
        f"- **Book**: {record['book'].get('title')} (id={record['book'].get('id')})",
        f"- **Location**: chapter {record['chapter_idx']}, paragraph {record['paragraph_idx']}",
        f"- **Model**: {record.get('model') or 'unknown'}",
        f"- **Trace**: {record.get('trace_id') or 'n/a'}",
        "",
        "## Target Paragraph",
        "",
        record.get("source_paragraph") or "_(excerpt omitted)_",
        "",
        "## AI Comment",
        "",
        f"**Type**: {record.get('comment_type')}",
        "",
        record.get("ai_comment", ""),
        "",
    ]

    window = record.get("window")
    if window:
        lines.extend(
            [
                "## Window",
                "",
                f"- seq={window.get('seq')} id={window.get('id')}",
                f"- focus: [{window.get('focus_start_paragraph_idx')}, "
                f"{window.get('focus_end_paragraph_idx')}]",
                f"- range: [{window.get('start_paragraph_idx')}, "
                f"{window.get('end_paragraph_idx')}]",
                "",
            ]
        )

    neighbors = record.get("neighbor_paragraphs") or []
    if neighbors:
        lines.append("## Neighbor Paragraphs")
        lines.append("")
        for item in neighbors:
            lines.append(f"### p={item.get('paragraph_idx')}")
            lines.append("")
            lines.append(item.get("text") or "")
            lines.append("")

    tokens = record.get("tokens") or {}
    if tokens or record.get("latency_ms") is not None:
        lines.extend(["## Metrics", ""])
        if record.get("latency_ms") is not None:
            lines.append(f"- latency_ms: {record['latency_ms']}")
        for key in ("input", "output", "cached_input"):
            if tokens.get(key) is not None:
                lines.append(f"- tokens.{key}: {tokens[key]}")
        lines.append("")

    return "\n".join(lines)
