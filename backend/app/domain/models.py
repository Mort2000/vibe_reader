from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _parse_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


@dataclass
class ReadingWindow:
    id: int
    book_id: int
    chapter_idx: int
    window_seq: int
    start_paragraph_idx: int
    end_paragraph_idx: int
    focus_start_paragraph_idx: int
    focus_end_paragraph_idx: int
    assistant_frontier_paragraph_idx: int
    text_hash: str = ""
    context_hash: str = ""
    status: str = "pending"
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ReadingWindow:
        return cls(
            id=int(row["id"]),
            book_id=int(row["book_id"]),
            chapter_idx=int(row["chapter_idx"]),
            window_seq=int(row["window_seq"]),
            start_paragraph_idx=int(row["start_paragraph_idx"]),
            end_paragraph_idx=int(row["end_paragraph_idx"]),
            focus_start_paragraph_idx=int(row["focus_start_paragraph_idx"]),
            focus_end_paragraph_idx=int(row["focus_end_paragraph_idx"]),
            assistant_frontier_paragraph_idx=int(
                row["assistant_frontier_paragraph_idx"]
            ),
            text_hash=row.get("text_hash") or "",
            context_hash=row.get("context_hash") or "",
            status=row.get("status", "pending"),
            error=row.get("error"),
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
            completed_at=row.get("completed_at"),
        )


@dataclass
class BookContextState:
    id: int
    book_id: int
    active_chapter_idx: int = 0
    reading_paragraph_idx: int = 0
    assistant_frontier_chapter_idx: int = 0
    assistant_frontier_paragraph_idx: int = 0
    context_frontier_chapter_idx: int = 0
    context_frontier_paragraph_idx: int = 0
    latest_summary_id: int | None = None
    live_l2_chunk_ids: list[int] = field(default_factory=list)
    compaction_epoch: int = 0
    status: str = "idle"
    running_job_id: int | None = None
    pending_chapter_idx: int | None = None
    pending_paragraph_idx: int | None = None
    pending_scroll_pct: float | None = None
    pending_assistant_frontier_chapter_idx: int | None = None
    pending_assistant_frontier_paragraph_idx: int | None = None
    pending_context_jump_chars: int | None = None
    pending_updated_at: str | None = None
    emergency_overflow_used: int = 0
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> BookContextState:
        return cls(
            id=int(row["id"]),
            book_id=int(row["book_id"]),
            active_chapter_idx=int(row.get("active_chapter_idx") or 0),
            reading_paragraph_idx=int(row.get("reading_paragraph_idx") or 0),
            assistant_frontier_chapter_idx=int(
                row.get("assistant_frontier_chapter_idx") or 0
            ),
            assistant_frontier_paragraph_idx=int(
                row.get("assistant_frontier_paragraph_idx") or 0
            ),
            context_frontier_chapter_idx=int(
                row.get("context_frontier_chapter_idx") or 0
            ),
            context_frontier_paragraph_idx=int(
                row.get("context_frontier_paragraph_idx") or 0
            ),
            latest_summary_id=row.get("latest_summary_id"),
            live_l2_chunk_ids=_parse_json(
                row.get("live_l2_chunk_ids_json"), []
            ),
            compaction_epoch=int(row.get("compaction_epoch") or 0),
            status=row.get("status", "idle"),
            running_job_id=row.get("running_job_id"),
            pending_chapter_idx=row.get("pending_chapter_idx"),
            pending_paragraph_idx=row.get("pending_paragraph_idx"),
            pending_scroll_pct=row.get("pending_scroll_pct"),
            pending_assistant_frontier_chapter_idx=row.get(
                "pending_assistant_frontier_chapter_idx"
            ),
            pending_assistant_frontier_paragraph_idx=row.get(
                "pending_assistant_frontier_paragraph_idx"
            ),
            pending_context_jump_chars=row.get("pending_context_jump_chars"),
            pending_updated_at=row.get("pending_updated_at"),
            emergency_overflow_used=int(row.get("emergency_overflow_used") or 0),
            last_error=row.get("last_error"),
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
        )


@dataclass
class OriginalTextChunk:
    id: int
    book_id: int
    chapter_idx: int
    chunk_seq: int
    start_paragraph_idx: int
    end_paragraph_idx: int
    token_estimate: int = 0
    char_count: int = 0
    text_hash: str = ""
    raw_token_estimate: int = 0
    estimator_model: str = ""
    estimator_version: str = ""
    estimator_calibration_ratio: float = 1.0
    chunking_version: str = ""
    status: str = "active"
    reclaimed_by_summary_id: int | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> OriginalTextChunk:
        return cls(
            id=int(row["id"]),
            book_id=int(row["book_id"]),
            chapter_idx=int(row["chapter_idx"]),
            chunk_seq=int(row["chunk_seq"]),
            start_paragraph_idx=int(row["start_paragraph_idx"]),
            end_paragraph_idx=int(row["end_paragraph_idx"]),
            token_estimate=int(row.get("token_estimate") or 0),
            char_count=int(row.get("char_count") or 0),
            text_hash=row.get("text_hash") or "",
            raw_token_estimate=int(row.get("raw_token_estimate") or 0),
            estimator_model=row.get("estimator_model") or "",
            estimator_version=row.get("estimator_version") or "",
            estimator_calibration_ratio=float(
                row.get("estimator_calibration_ratio") or 1.0
            ),
            chunking_version=row.get("chunking_version") or "",
            status=row.get("status", "active"),
            reclaimed_by_summary_id=row.get("reclaimed_by_summary_id"),
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
        )


@dataclass
class ChapterCompressedSummary:
    id: int
    book_id: int
    chapter_idx: int
    covered_start_paragraph_idx: int
    covered_end_paragraph_idx: int
    source_chunk_ids: list[int] = field(default_factory=list)
    source_text_hash: str = ""
    summary: str = ""
    anchor_excerpts: list[dict[str, Any]] = field(default_factory=list)
    token_estimate: int = 0
    context_version: int = 1
    compaction_epoch: int = 0
    created_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ChapterCompressedSummary:
        return cls(
            id=int(row["id"]),
            book_id=int(row["book_id"]),
            chapter_idx=int(row["chapter_idx"]),
            covered_start_paragraph_idx=int(
                row["covered_start_paragraph_idx"]
            ),
            covered_end_paragraph_idx=int(row["covered_end_paragraph_idx"]),
            source_chunk_ids=_parse_json(
                row.get("source_chunk_ids_json"), []
            ),
            source_text_hash=row.get("source_text_hash") or "",
            summary=row.get("summary") or "",
            anchor_excerpts=_parse_json(
                row.get("anchor_excerpts_json"), []
            ),
            token_estimate=int(row.get("token_estimate") or 0),
            context_version=int(row.get("context_version") or 1),
            compaction_epoch=int(row.get("compaction_epoch") or 0),
            created_at=row.get("created_at") or "",
        )
