"""V-15: Audit sample exporter — comment samples for subjective review."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .core.config import VerifyConfig
from .core.run_manager import RunManager

if TYPE_CHECKING:
    from .core.context import ScenarioContext

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
    llm_mode: str = "stub"
    stub_profile: str | None = "mvp_default"
    usage_source: str = "estimate"


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
        llm_mode: str = "stub",
        stub_profile: str | None = "mvp_default",
        usage_source: str = "estimate",
    ) -> list[str]:
        """Sample up to ``sample_comments_per_window`` comments from one window."""
        if not comments:
            return []

        per_window = self.config.audit.sample_comments_per_window
        window_id = window.get("id") if window else None
        grouped = [
            c for c in comments if window_id is None or c.get("window_id") == window_id
        ]
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
            draft.llm_mode = llm_mode
            draft.stub_profile = stub_profile
            draft.usage_source = usage_source
            sample_ids.append(self.add_comment(draft))
        return sample_ids

    def record_window_status(
        self,
        *,
        scenario_id: str,
        book: dict[str, Any],
        chapter_idx: int,
        window: dict[str, Any] | None,
        no_call: bool = False,
        validation_failures: list[dict[str, Any]] | None = None,
    ) -> None:
        """Record a window with no audit comments (no-call or validation-only)."""
        record = {
            "run_id": self.run_manager.run_id,
            "scenario_id": scenario_id,
            "book": {"id": book.get("id"), "title": book.get("title")},
            "chapter_idx": chapter_idx,
            "window": _window_payload(window),
            "no_call": no_call,
            "llm_mode": self.config.llm.mode,
            "stub_profile": self.config.llm.stub_profile
            if not self.config.is_real_llm
            else None,
            "usage_source": self.config.usage_source,
            "validation_failures": validation_failures or [],
        }
        audit_dir = self.run_manager.base_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        self.run_manager.write_ndjson("audit/window_status.ndjson", [record])

    def export(self) -> tuple[int, int]:
        """Write ``audit/comments.ndjson`` and markdown samples."""
        if not self._drafts:
            return 0, 0

        records = [
            self._to_record(sample_id, draft) for sample_id, draft in self._drafts
        ]
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
            "llm_mode": draft.llm_mode,
            "stub_profile": draft.stub_profile,
            "model": draft.model,
            "trace_id": draft.trace_id,
            "prompt_version": draft.prompt_version,
            "context_hash": draft.context_hash,
            "usage_source": draft.usage_source,
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
        f"- **LLM mode**: {record.get('llm_mode') or 'unknown'}",
        f"- **Usage source**: {record.get('usage_source') or 'unknown'}",
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


MAX_CHAT_EXCERPT_CHARS = 600


@dataclass
class ChatSampleDraft:
    scenario_id: str
    book: dict[str, Any]
    chapter_idx: int
    paragraph_idx: int
    session_id: int | None
    turn_id: int | None
    user_msg: str
    ai_msg: str
    source_paragraph: str = ""
    neighbor_paragraphs: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    trace_id: str = ""
    prompt_version: str = ""
    context_hash: str = ""
    tokens: dict[str, Any] = field(default_factory=dict)
    ttft_ms: float | None = None
    total_ms: float | None = None
    delta_count: int = 0
    followup_of_sample_id: str | None = None
    recent_chat_tokens: int | None = None
    recent_chat_turns_clipped_count: int | None = None
    llm_mode: str = "stub"
    stub_profile: str | None = "mvp_default"
    usage_source: str = "estimate"


def ensure_chat_audit_exporter(ctx: ScenarioContext) -> ChatAuditExporter:
    """Return the scenario chat exporter, creating one if setup did not."""
    if ctx.chat_audit_exporter is None:
        ctx.chat_audit_exporter = ChatAuditExporter(ctx.run_manager, ctx.config)
    return ctx.chat_audit_exporter


class ChatAuditExporter:
    """Collects and writes chat audit samples (V-15 dialogue portion)."""

    def __init__(self, run_manager: RunManager, config: VerifyConfig):
        self.run_manager = run_manager
        self.config = config
        self._drafts: list[tuple[str, ChatSampleDraft]] = []
        self._counter = 0
        self._sample_id_by_index: dict[int, str] = {}

    @property
    def sample_count(self) -> int:
        return len(self._drafts)

    def add_turn(
        self,
        draft: ChatSampleDraft,
        *,
        turn_index: int = 0,
        followup_of_index: int | None = None,
    ) -> str:
        self._counter += 1
        sample_id = f"chat_{draft.scenario_id}_{self._counter:04d}"
        if followup_of_index is not None:
            draft.followup_of_sample_id = self._sample_id_by_index.get(followup_of_index)
        self._drafts.append((sample_id, draft))
        self._sample_id_by_index[turn_index] = sample_id
        return sample_id

    def add_turns_from_records(
        self,
        turns: list[Any],
        *,
        scenario_id: str,
        book: dict[str, Any],
        paragraphs: list[dict[str, Any]] | None = None,
        model: str = "",
        llm_mode: str = "stub",
        stub_profile: str | None = "mvp_default",
        usage_source: str = "estimate",
        trace_meta_by_trace_id: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]:
        """Add chat turns from ``ChatTurnRecord`` objects produced by flows.chat."""
        per_probe = self.config.audit.sample_chat_turns_per_probe
        selected = turns[:per_probe]
        text_map = {p["paragraph_idx"]: p.get("text", "") for p in (paragraphs or [])}
        sample_ids: list[str] = []
        for index, turn in enumerate(selected):
            result = turn.result
            trace_id = result.trace_id or ""
            trace_meta = (trace_meta_by_trace_id or {}).get(trace_id, {})
            pidx = turn.paragraph_idx
            draft = ChatSampleDraft(
                scenario_id=scenario_id,
                book=book,
                chapter_idx=turn.chapter_idx,
                paragraph_idx=pidx,
                session_id=result.session_id,
                turn_id=result.turn_id,
                user_msg=turn.user_msg,
                ai_msg=result.full_text,
                source_paragraph=_excerpt(
                    text_map.get(pidx, turn.source_paragraph_excerpt),
                    self.config.audit.include_original_excerpts,
                ),
                neighbor_paragraphs=_neighbor_excerpts(
                    text_map,
                    pidx,
                    self.config.audit.include_original_excerpts,
                ),
                model=model,
                trace_id=trace_id,
                prompt_version=trace_meta.get("prompt_version", ""),
                context_hash=trace_meta.get("context_hash", ""),
                tokens={
                    "input": result.tokens_in,
                    "output": result.tokens_out,
                },
                ttft_ms=result.ttft_ms,
                total_ms=result.total_ms,
                delta_count=len(result.deltas),
                recent_chat_tokens=turn.recent_chat_tokens,
                recent_chat_turns_clipped_count=turn.recent_chat_turns_clipped_count,
            )
            draft.llm_mode = llm_mode
            draft.stub_profile = stub_profile
            draft.usage_source = usage_source
            sample_ids.append(
                self.add_turn(
                    draft,
                    turn_index=index,
                    followup_of_index=turn.followup_of,
                )
            )
        return sample_ids

    def export(self) -> tuple[int, int]:
        """Write ``audit/chats.ndjson`` and markdown samples."""
        if not self._drafts:
            return 0, 0

        records = [self._to_record(sample_id, draft) for sample_id, draft in self._drafts]
        audit_dir = self.run_manager.base_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        samples_dir = audit_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        ndjson_path = audit_dir / "chats.ndjson"
        with open(ndjson_path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        md_count = 0
        for record in records:
            md_path = samples_dir / f"{record['sample_id']}.md"
            md_path.write_text(_render_chat_markdown(record), encoding="utf-8")
            md_count += 1

        return len(records), md_count

    def _to_record(self, sample_id: str, draft: ChatSampleDraft) -> dict[str, Any]:
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
            "session_id": draft.session_id,
            "turn_id": draft.turn_id,
            "source_paragraph": draft.source_paragraph,
            "neighbor_paragraphs": draft.neighbor_paragraphs,
            "user_msg": draft.user_msg,
            "ai_msg": draft.ai_msg,
            "followup_of_sample_id": draft.followup_of_sample_id,
            "recent_chat_tokens": draft.recent_chat_tokens,
            "recent_chat_turns_clipped_count": draft.recent_chat_turns_clipped_count,
            "llm_mode": draft.llm_mode,
            "stub_profile": draft.stub_profile,
            "model": draft.model,
            "trace_id": draft.trace_id,
            "prompt_version": draft.prompt_version,
            "context_hash": draft.context_hash,
            "usage_source": draft.usage_source,
            "tokens": draft.tokens,
            "ttft_ms": draft.ttft_ms,
            "total_ms": draft.total_ms,
            "delta_count": draft.delta_count,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def _render_chat_markdown(record: dict[str, Any]) -> str:
    lines = [
        f"# Chat Sample `{record['sample_id']}`",
        "",
        f"- **Scenario**: {record['scenario_id']}",
        f"- **Book**: {record['book'].get('title')} (id={record['book'].get('id')})",
        f"- **Location**: chapter {record['chapter_idx']}, paragraph {record['paragraph_idx']}",
        f"- **Session**: {record.get('session_id') or 'n/a'} turn={record.get('turn_id') or 'n/a'}",
        f"- **Model**: {record.get('model') or 'unknown'}",
        f"- **LLM mode**: {record.get('llm_mode') or 'unknown'}",
        f"- **Usage source**: {record.get('usage_source') or 'unknown'}",
        f"- **Trace**: {record.get('trace_id') or 'n/a'}",
        "",
        "## User Message",
        "",
        record.get("user_msg") or "",
        "",
        "## AI Response",
        "",
        record.get("ai_msg") or "",
        "",
    ]

    if record.get("followup_of_sample_id"):
        lines.extend(
            [
                "## Follow-up",
                "",
                f"- prior sample: `{record['followup_of_sample_id']}`",
                "",
            ]
        )

    source = record.get("source_paragraph")
    if source:
        lines.extend(["## Target Paragraph", "", source, ""])

    neighbors = record.get("neighbor_paragraphs") or []
    if neighbors:
        lines.extend(["## Neighbor Paragraphs", ""])
        for item in neighbors:
            lines.append(f"### p={item.get('paragraph_idx')}")
            lines.append("")
            lines.append(item.get("text") or "")
            lines.append("")

    metrics_lines = []
    if record.get("ttft_ms") is not None:
        metrics_lines.append(f"- chat.ttft_ms: {record['ttft_ms']}")
    if record.get("total_ms") is not None:
        metrics_lines.append(f"- chat.total_ms: {record['total_ms']}")
    tokens = record.get("tokens") or {}
    for key in ("input", "output"):
        if tokens.get(key) is not None:
            metrics_lines.append(f"- chat.tokens.{key}: {tokens[key]}")
    if record.get("delta_count") is not None:
        metrics_lines.append(f"- delta_count: {record['delta_count']}")
    if metrics_lines:
        lines.extend(["## Metrics", ""] + metrics_lines + [""])

    return "\n".join(lines)
