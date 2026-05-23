from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import Settings

logger = logging.getLogger(__name__)

COMMENT_TYPES = Literal["observation", "question", "humor", "craft", "warning"]

COMMENT_INSTRUCTIONS = """\
你是一位中文小说阅读伴侣。为指定段落生成简短评论。
规则：每条评论只针对一个段落；建议 20-80 中文字；不编造文中没有的内容；
comment_type 必须是 observation/question/humor/craft/warning 之一。"""


class ParagraphCommentDraft(BaseModel):
    paragraph_idx: int
    comment: str
    comment_type: COMMENT_TYPES = "observation"


class ParagraphCommentBatch(BaseModel):
    comments: list[ParagraphCommentDraft]


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


_comment_agent: Agent | None = None


def get_comment_agent(settings: Settings) -> Agent:
    global _comment_agent
    if _comment_agent is not None:
        return _comment_agent
    model = get_llm_model(settings)
    _comment_agent = Agent(
        model,
        output_type=ParagraphCommentBatch,
        instructions=COMMENT_INSTRUCTIONS,
        name="ParagraphCommentAgent",
        description="为阅读窗口目标段落生成单段评论",
        retries={"output": 2},
    )
    return _comment_agent
