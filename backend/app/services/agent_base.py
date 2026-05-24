from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import Settings

logger = logging.getLogger(__name__)

COMMENT_TYPES = Literal["observation", "question", "humor", "craft", "warning"]

COMMENT_INSTRUCTIONS = """\
你是一位中文小说阅读伴侣。为指定段落生成简短评论。
你可以调用 emit_comment 提交评论，也可以不调用。
每次 emit_comment 只提交一条评论。
只评论 comment_target_paragraphs 中的段落。
不要为了满足密度提示生成空泛、重复、跨段或剧透评论。
规则：每条评论只针对一个段落；建议 20-80 中文字；不编造文中没有的内容；
comment_type 必须是 observation/question/humor/craft/warning 之一。
最终自然语言文本会被忽略。"""


class EmitCommentDraft(BaseModel):
    paragraph_idx: int
    comment: str = Field(min_length=1)
    comment_type: COMMENT_TYPES = "observation"


@dataclass
class CommentDensityHint:
    stat_start_paragraph_idx: int
    stat_end_paragraph_idx: int
    stat_target_paragraph_count: int
    active_comment_count: int
    soft_min_density: float
    current_density: float
    estimated_missing_comments: int


@dataclass
class CommentDeps:
    target_paragraph_ids: set[int] = field(default_factory=set)
    density_hint: CommentDensityHint | None = None
    raw_tool_payloads: list[dict[str, Any]] = field(default_factory=list)


class AnchorExcerpt(BaseModel):
    chapter_idx: int
    paragraph_idx: int | None = None
    text: str
    reason: str


class ChapterCompressedSummaryOutput(BaseModel):
    summary: str
    anchor_excerpts: list[AnchorExcerpt] = Field(default_factory=list)


COMPACTION_INSTRUCTIONS = """\
你是一位中文小说阅读助手，负责将已读章节原文压缩成结构化摘要。

输入：
- 上一份章节压缩摘要（可以为空）
- 最早的一个完整原文 chunk

输出规则：
- 压缩结果必须短于输入。
- 保留关键情节、人物动作、场景变化和重要对话。
- anchor_excerpts 保留原文中关键的锚点片段，每个不超过 120 tokens。
- 不要编造文中没有的内容。
- 不要输出完整人物关系图或时间线。
- 不要输出 comment digest 或 chat digest。
- summary 和 anchor_excerpts 都以 JSON 结构化输出。"""


_model: OpenAIChatModel | None = None


def get_llm_model(settings: Settings) -> OpenAIChatModel:
    global _model
    if _model is not None:
        return _model
    _model = OpenAIChatModel(
        settings.llm.model,
        provider=OpenAIProvider(
            base_url=settings.llm.base_url,
            api_key=settings.llm.api_key,
        ),
    )
    return _model


_comment_agent: Agent[CommentDeps, str | None] | None = None


def get_comment_agent(settings: Settings) -> Agent[CommentDeps, str | None]:
    global _comment_agent
    if _comment_agent is not None:
        return _comment_agent
    model = get_llm_model(settings)
    _comment_agent = Agent(
        model,
        deps_type=CommentDeps,
        output_type=str | None,
        instructions=COMMENT_INSTRUCTIONS,
        name="ParagraphCommentAgent",
        description="为阅读窗口目标段落生成单段评论",
        retries={"output": 2},
    )

    @_comment_agent.tool
    async def emit_comment(
        ctx: RunContext[CommentDeps],
        paragraph_idx: int,
        comment: str,
        comment_type: COMMENT_TYPES = "observation",
    ) -> str:
        """Submit one paragraph comment draft."""
        ctx.deps.raw_tool_payloads.append({
            "paragraph_idx": paragraph_idx,
            "comment": comment,
            "comment_type": comment_type,
        })
        return "accepted"

    return _comment_agent


@dataclass
class CompactionDeps:
    previous_summary: str | None = None
    chunk_text: str = ""
    raw_output: dict[str, Any] = field(default_factory=dict)


_compaction_agent: Agent[CompactionDeps, str | None] | None = None


def get_compaction_agent(
    settings: Settings,
) -> Agent[CompactionDeps, str | None]:
    global _compaction_agent
    if _compaction_agent is not None:
        return _compaction_agent
    model = get_llm_model(settings)
    _compaction_agent = Agent(
        model,
        deps_type=CompactionDeps,
        output_type=str | None,
        instructions=COMPACTION_INSTRUCTIONS
        + "\n调用 emit_chapter_compressed_summary 提交 JSON 结构化摘要；最终自然语言会被忽略。",
        name="ContextCompactionAgent",
        description="将已读原文 chunk 压缩成章节摘要",
        retries={"output": 2},
    )

    @_compaction_agent.tool
    async def emit_chapter_compressed_summary(
        ctx: RunContext[CompactionDeps], payload: dict[str, Any]
    ) -> str:
        """Submit chapter compressed summary payload."""
        ctx.deps.raw_output = payload
        return "accepted"

    return _compaction_agent
