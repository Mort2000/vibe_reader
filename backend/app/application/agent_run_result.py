from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from ..domain.models import (
    ChapterCompressedSummary,
    OriginalTextChunk,
    ReadingWindow,
)
from ..services.agent_base import CommentDensityHint


@dataclass
class CommentAuditContext:
    trace_id: str
    book: dict[str, Any]
    chapter_idx: int
    window: ReadingWindow
    window_paragraphs: list[dict[str, Any]]
    target_paragraphs: list[int]
    density_hint: CommentDensityHint | None
    prompt: str
    agent_result: Any
    raw_payloads: list[dict[str, Any]]
    valid_comments: list[dict[str, Any]]
    discarded: list[dict[str, Any]]
    validation_failed_count: int
    no_call: bool
    usage_source: str = "estimate"
    context_manifest: dict[str, Any] | None = None


@dataclass
class CompactionAuditContext:
    trace_id: str
    source_chunk: OriginalTextChunk
    previous_summary_row: ChapterCompressedSummary | None
    next_summary_row: ChapterCompressedSummary
    prompt: str
    agent_result: Any
    transaction_committed: bool
    prompt_manifest: dict[str, Any] | None = None


@dataclass
class ChatAuditContext:
    trace_id: str
    book_id: int
    chapter_idx: int
    paragraph_idx: int
    prompt: str
    agent_result: Any
    recent_chat_turns: list[dict[str, Any]]
    user_msg: str = ""
    prompt_manifest: dict[str, Any] | None = None


AuditContext = Union[CommentAuditContext, CompactionAuditContext, ChatAuditContext]


@dataclass
class AgentRunResult:
    agent_name: str
    duration_ms: float

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None

    context_hash: str = ""
    context_estimated_tokens: int = 0
    prompt_version: str = ""
    prompt_manifest: dict[str, Any] | None = None
    usage_scope: str = "single_request"

    no_call: bool = False
    tool_call_count: int = 0
    valid_count: int = 0
    validation_failed_count: int = 0
    discarded_count: int = 0
    discarded_by_reason: dict[str, int] = field(default_factory=dict)
    candidate_lookup_count: int = 0
    comment_density_actual: float = 0.0
    comment_density_soft_min: float = 0.0
    density_stat_start: int = 0
    density_stat_end: int = 0

    source_chunk_id: int | None = None
    reclaimed_chunk_id: int | None = None
    summary_id: int | None = None
    compaction_epoch: int | None = None
    transaction_committed: bool | None = None
    source_chunk_tokens: int = 0
    source_paragraph_count: int = 0
    source_chunk_hash: str = ""
    source_chunk_start: int = 0
    source_chunk_end: int = 0
    previous_summary_id: int | None = None
    compaction_source: dict[str, Any] | None = None

    preflight_triggered: bool = False
    hard_triggered: bool = False
    context_degraded: bool = False
    missing_target_original_count: int = 0

    invocation_id: str = ""
    audit_context: AuditContext | None = None

    def to_telemetry_dict(self, interaction_path: str = "") -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "duration_ms": self.duration_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "no_call": self.no_call,
            "tool_call_count": self.tool_call_count,
            "valid_count": self.valid_count,
            "validation_failed_count": self.validation_failed_count,
            "discarded_count": self.discarded_count,
            "discarded_by_reason": self.discarded_by_reason,
            "candidate_lookup_count": self.candidate_lookup_count,
            "context_hash": self.context_hash,
            "comment_density_actual": self.comment_density_actual,
            "comment_density_soft_min": self.comment_density_soft_min,
            "density_stat_start": self.density_stat_start,
            "density_stat_end": self.density_stat_end,
            "invocation_id": self.invocation_id,
            "interaction_path": interaction_path,
            "context_estimated_tokens": self.context_estimated_tokens,
            "preflight_triggered": self.preflight_triggered,
            "hard_triggered": self.hard_triggered,
            "context_degraded": self.context_degraded,
            "prompt_version": self.prompt_version,
            "prompt_manifest": self.prompt_manifest,
            "usage_scope": self.usage_scope,
        }
