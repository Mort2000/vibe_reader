from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import Settings
from .llm_model import CompatibleOpenAIChatModel

logger = logging.getLogger(__name__)

COMMENT_TYPES = Literal["observation", "question", "humor", "craft", "warning"]

COMMENT_INSTRUCTIONS = """\
你是一位中文小说阅读伴侣。为指定段落生成简短评论。
你可以调用 emit_comment 提交评论，也可以不调用。
每次 emit_comment 只提交一条评论。
如果需要提交多条评论，优先在同一轮响应中并行调用多次 emit_comment，而不是一轮只调用一次。
只评论 comment_target_paragraphs 中的段落。
不要为了满足密度提示生成空泛、重复、跨段或剧透评论。
规则：每条评论只针对一个段落；建议 20-80 中文字；不编造文中没有的内容；
comment_type 必须是 observation/question/humor/craft/warning 之一。
完成所需 tool call 后不要再输出自然语言；最终自然语言文本会被忽略。"""


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
  每项可为原文引文字符串，或对象 {text, paragraph_idx, reason}；reason 用一句话说明保留该句的原因。
- 不要编造文中没有的内容。
- 不要输出完整人物关系图或时间线。
- 不要输出 comment digest 或 chat digest。
- summary 和 anchor_excerpts 都以 JSON 结构化输出。"""


ModelCacheKey = tuple[str, str, str, str, str]
AgentCacheKey = tuple[str, ModelCacheKey]

_models: dict[ModelCacheKey, CompatibleOpenAIChatModel] = {}
_comment_agents: dict[AgentCacheKey, Agent[CommentDeps, str | None]] = {}
_chat_agents: dict[AgentCacheKey, Agent[ChatDeps, str]] = {}
_compaction_agents: dict[AgentCacheKey, Agent[CompactionDeps, str | None]] = {}


def _secret_fingerprint(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _model_cache_key(settings: Settings, agent: str = "global") -> ModelCacheKey:
    llm = settings.effective_llm(agent)
    return (
        llm.provider,
        llm.base_url,
        llm.model,
        llm.think_effort,
        _secret_fingerprint(llm.api_key),
    )


def clear_agent_caches() -> None:
    _models.clear()
    _comment_agents.clear()
    _chat_agents.clear()
    _compaction_agents.clear()


def get_llm_model(
    settings: Settings,
    agent: str = "global",
) -> CompatibleOpenAIChatModel:
    cache_key = _model_cache_key(settings, agent)
    cached = _models.get(cache_key)
    if cached is not None:
        return cached

    llm = settings.effective_llm(agent)
    model = CompatibleOpenAIChatModel(
        llm.model,
        provider=OpenAIProvider(
            base_url=llm.base_url,
            api_key=llm.api_key,
        ),
    )
    _models[cache_key] = model
    return model


def get_comment_agent(settings: Settings) -> Agent[CommentDeps, str | None]:
    cache_key = ("ParagraphCommentAgent", _model_cache_key(settings, "comment"))
    cached = _comment_agents.get(cache_key)
    if cached is not None:
        return cached

    model = get_llm_model(settings, "comment")
    agent = Agent(
        model,
        deps_type=CommentDeps,
        output_type=str | None,
        instructions=COMMENT_INSTRUCTIONS,
        name="ParagraphCommentAgent",
        description="为阅读窗口目标段落生成单段评论",
        retries={"output": 2},
    )

    @agent.tool
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

    _comment_agents[cache_key] = agent
    return agent


@dataclass
class CompactionDeps:
    previous_summary: str | None = None
    chunk_text: str = ""
    raw_output: dict[str, Any] = field(default_factory=dict)


CHAT_INSTRUCTIONS = """\
你是一位中文小说阅读伴侣。用户正在阅读一本小说，当前有一个阅读位置和对应的上下文。
请根据上下文回答用户的问题。
规则：
- 回答要围绕当前阅读位置附近的内容。
- 不要编造文中没有的内容。
- 如果上下文不足以回答，请如实说明。
- 用中文回答，简洁明了。"""


@dataclass
class ChatDeps:
    pass


def get_chat_agent(settings: Settings) -> Agent[ChatDeps, str]:
    cache_key = ("ReadingChatAgent", _model_cache_key(settings, "chat"))
    cached = _chat_agents.get(cache_key)
    if cached is not None:
        return cached

    model = get_llm_model(settings, "chat")
    agent = Agent(
        model,
        deps_type=ChatDeps,
        output_type=str,
        instructions=CHAT_INSTRUCTIONS,
        name="ReadingChatAgent",
        description="围绕当前阅读位置回答用户提问",
        retries={"output": 1},
    )
    _chat_agents[cache_key] = agent
    return agent


def get_compaction_agent(
    settings: Settings,
) -> Agent[CompactionDeps, str | None]:
    cache_key = ("ContextCompactionAgent", _model_cache_key(settings, "comment"))
    cached = _compaction_agents.get(cache_key)
    if cached is not None:
        return cached

    model = get_llm_model(settings, "comment")
    agent = Agent(
        model,
        deps_type=CompactionDeps,
        output_type=str | None,
        instructions=COMPACTION_INSTRUCTIONS
        + "\n调用 emit_chapter_compressed_summary 提交 JSON 结构化摘要；最终自然语言会被忽略。",
        name="ContextCompactionAgent",
        description="将已读原文 chunk 压缩成章节摘要",
        retries={"output": 2},
    )

    @agent.tool
    async def emit_chapter_compressed_summary(
        ctx: RunContext[CompactionDeps], payload: dict[str, Any]
    ) -> str:
        """Submit chapter compressed summary payload."""
        ctx.deps.raw_output = payload
        return "accepted"

    _compaction_agents[cache_key] = agent
    return agent
