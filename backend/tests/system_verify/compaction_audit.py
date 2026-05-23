"""Export chapter compression summary and L2 chunk audit samples (V-09 / A3)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import VerifyConfig
from .context_assertions import extract_chapter_summary, extract_l2_chunks
from .run import RunManager

MAX_SUMMARY_EXCERPT = 600


@dataclass
class CompactionSampleDraft:
    scenario_id: str
    book: dict[str, Any]
    chapter_idx: int
    summary: dict[str, Any]
    source_chunk: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    invocation_id: str = ""
    model: str = ""
    llm_mode: str = "stub"
    usage_source: str = "estimate"
    tokens: dict[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None
    compaction_epoch: int | None = None
    l2_chunks: list[dict[str, Any]] = field(default_factory=list)


class CompactionAuditExporter:
    """Writes compaction summary samples and L2 chunk manifest artifacts."""

    def __init__(self, run_manager: RunManager, config: VerifyConfig):
        self.run_manager = run_manager
        self.config = config
        self._drafts: list[tuple[str, CompactionSampleDraft]] = []
        self._counter = 0
        self._l2_manifest_records: list[dict[str, Any]] = []
        self._prompt_manifest_records: list[dict[str, Any]] = []

    def add_compaction_run(
        self,
        agent_run: dict[str, Any],
        *,
        scenario_id: str,
        book: dict[str, Any],
        chapter_idx: int,
        model: str,
        llm_mode: str,
        usage_source: str,
        tokens: dict[str, Any] | None = None,
        latency_ms: float | None = None,
        l2_chunks: list[dict[str, Any]] | None = None,
    ) -> str | None:
        interaction = agent_run.get("interaction") or agent_run
        summary = extract_chapter_summary(interaction)
        if not summary:
            return None

        self._counter += 1
        sample_id = f"compaction_{scenario_id}_{self._counter:04d}"
        draft = CompactionSampleDraft(
            scenario_id=scenario_id,
            book=book,
            chapter_idx=chapter_idx,
            summary=summary,
            source_chunk=(
                interaction.get("compaction_source")
                or interaction.get("source_chunk")
                or {}
            ),
            trace_id=str(agent_run.get("trace_id") or interaction.get("trace_id") or ""),
            invocation_id=str(
                agent_run.get("invocation_id") or interaction.get("invocation_id") or ""
            ),
            model=model,
            llm_mode=llm_mode,
            usage_source=usage_source,
            tokens=tokens or {},
            latency_ms=latency_ms,
            compaction_epoch=summary.get("compaction_epoch")
            or interaction.get("compaction_epoch"),
            l2_chunks=l2_chunks or extract_l2_chunks(interaction.get("injected_context") or {}),
        )
        self._drafts.append((sample_id, draft))
        return sample_id

    def add_l2_manifest(
        self,
        *,
        scenario_id: str,
        step_id: str,
        chapter_idx: int,
        paragraph_idx: int,
        injected_context: dict[str, Any],
    ) -> None:
        chunks = extract_l2_chunks(injected_context)
        if not chunks:
            return
        self._l2_manifest_records.append(
            {
                "scenario_id": scenario_id,
                "step_id": step_id,
                "chapter_idx": chapter_idx,
                "paragraph_idx": paragraph_idx,
                "context_hash": injected_context.get("context_hash"),
                "total_input_token_estimate": injected_context.get(
                    "total_input_token_estimate"
                ),
                "chunks": chunks,
                "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )

    def add_prompt_manifest_entry(
        self,
        *,
        invocation_id: str,
        agent: str,
        scenario_id: str,
        step_id: str,
        prompt_path: str,
        context_hash: str,
        token_estimate: int | None,
    ) -> None:
        if not self.config.audit.include_prompt_manifest:
            return
        self._prompt_manifest_records.append(
            {
                "invocation_id": invocation_id,
                "agent": agent,
                "scenario_id": scenario_id,
                "step_id": step_id,
                "prompt_path": prompt_path,
                "context_hash": context_hash,
                "input_token_estimate": token_estimate,
            }
        )

    def export(self) -> dict[str, int]:
        audit_dir = self.run_manager.base_dir / "audit"
        samples_dir = audit_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        ndjson_rows: list[dict[str, Any]] = []
        for sample_id, draft in self._drafts:
            row = {
                "sample_id": sample_id,
                "task_type": "chapter_compaction",
                "scenario_id": draft.scenario_id,
                "book_id": draft.book.get("id"),
                "book_title": draft.book.get("title"),
                "chapter_idx": draft.chapter_idx,
                "trace_id": draft.trace_id,
                "invocation_id": draft.invocation_id,
                "model": draft.model,
                "llm_mode": draft.llm_mode,
                "usage_source": draft.usage_source,
                "tokens": draft.tokens,
                "latency_ms": draft.latency_ms,
                "compaction_epoch": draft.compaction_epoch,
                "summary_excerpt": _excerpt(str(draft.summary.get("summary") or "")),
                "anchor_excerpt_count": len(draft.summary.get("anchor_excerpts") or []),
                "covered_start_paragraph_idx": draft.summary.get("covered_start_paragraph_idx"),
                "covered_end_paragraph_idx": draft.summary.get("covered_end_paragraph_idx"),
                "source_chunk": draft.source_chunk,
                "forbidden_fields_absent": {
                    field: field not in draft.summary
                    for field in ("comment_digest", "chat_digest")
                },
            }
            ndjson_rows.append(row)
            md_path = samples_dir / f"{sample_id}.md"
            md_path.write_text(_render_markdown(sample_id, draft), encoding="utf-8")

        if ndjson_rows:
            path = audit_dir / "compaction_summaries.ndjson"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ndjson_rows),
                encoding="utf-8",
            )

        l2_count = 0
        if self._l2_manifest_records:
            l2_path = audit_dir / "l2_chunk_manifest.ndjson"
            l2_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False) + "\n"
                    for row in self._l2_manifest_records
                ),
                encoding="utf-8",
            )
            l2_count = len(self._l2_manifest_records)

        prompt_count = 0
        if self._prompt_manifest_records:
            prompt_path = audit_dir / "prompt_manifest_index.ndjson"
            prompt_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False) + "\n"
                    for row in self._prompt_manifest_records
                ),
                encoding="utf-8",
            )
            prompt_count = len(self._prompt_manifest_records)

        return {
            "compaction_summaries_ndjson": len(ndjson_rows),
            "compaction_markdown": len(ndjson_rows),
            "l2_chunk_manifest": l2_count,
            "prompt_manifest_index": prompt_count,
        }


def _excerpt(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_SUMMARY_EXCERPT:
        return text
    return text[:MAX_SUMMARY_EXCERPT] + "...(truncated)"


def _render_markdown(sample_id: str, draft: CompactionSampleDraft) -> str:
    anchors = draft.summary.get("anchor_excerpts") or []
    lines = [
        f"# Compaction Sample `{sample_id}`",
        "",
        f"- scenario: `{draft.scenario_id}`",
        f"- chapter_idx: {draft.chapter_idx}",
        f"- trace_id: `{draft.trace_id}`",
        f"- invocation_id: `{draft.invocation_id}`",
        f"- llm_mode: `{draft.llm_mode}`",
        f"- model: `{draft.model}`",
        "",
        "## Summary",
        "",
        str(draft.summary.get("summary") or ""),
        "",
        "## Anchor Excerpts",
        "",
    ]
    if not anchors:
        lines.append("_none_")
    else:
        for anchor in anchors[:5]:
            if isinstance(anchor, dict):
                lines.append(
                    f"- P{anchor.get('paragraph_idx')}: "
                    f"{_excerpt(str(anchor.get('text') or ''))}"
                )
            else:
                lines.append(f"- {_excerpt(str(anchor))}")
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            f"- covered_start_paragraph_idx: {draft.summary.get('covered_start_paragraph_idx')}",
            f"- covered_end_paragraph_idx: {draft.summary.get('covered_end_paragraph_idx')}",
            f"- compaction_epoch: {draft.compaction_epoch}",
            "",
            "## L2 Chunk Manifest Snapshot",
            "",
            "```json",
            json.dumps(draft.l2_chunks[:5], ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)
