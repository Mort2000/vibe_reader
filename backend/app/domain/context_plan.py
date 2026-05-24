from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LiveOriginalChunkSelection:
    block_text: str = ""
    chunk_ids: list[int] = field(default_factory=list)
    partial_chunk_id: int | None = None
    partial_frontier_paragraph_idx: int | None = None
    estimated_tokens: int = 0


@dataclass
class ContextPlan:
    chapter_idx: int
    frontier: int
    live_start: int

    summary_text: str = ""
    summary_id: int | None = None
    summary_tokens: int = 0
    compaction_epoch: int = 0

    live_chunks: LiveOriginalChunkSelection = field(
        default_factory=LiveOriginalChunkSelection
    )

    comments_text: str = ""
    comments_tokens: int = 0

    task_text: str = ""
    task_tokens: int = 0

    book_title: str | None = None
    chapter_title: str | None = None

    system_tokens: int = 3_000
    metadata_tokens: int = 800
    reserved_tokens: int = 0

    @property
    def estimated_tokens(self) -> int:
        return (
            self.system_tokens
            + self.metadata_tokens
            + self.reserved_tokens
            + self.summary_tokens
            + self.live_chunks.estimated_tokens
            + self.comments_tokens
            + self.task_tokens
        )
