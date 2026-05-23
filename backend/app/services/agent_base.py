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
    paragraph_idx: int
    text: str
    reason: str


class RollingContextSnapshotOutput(BaseModel):
    summary: str
    comment_digest: str
    chat_digest: str
    anchor_excerpts: list[AnchorExcerpt] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


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
        ctx: RunContext[CommentDeps], payload: dict[str, Any]
    ) -> str:
        """Submit one paragraph comment draft.

        Expected payload:
        - paragraph_idx: target paragraph index
        - comment: short Chinese comment
        - comment_type: observation/question/humor/craft/warning
        """
        ctx.deps.raw_tool_payloads.append(payload)
        return "accepted"

    return _comment_agent
