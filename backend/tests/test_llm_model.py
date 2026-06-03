from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.providers.openai import OpenAIProvider

from app.services.llm_model import CompatibleOpenAIChatModel


def _model() -> CompatibleOpenAIChatModel:
    return CompatibleOpenAIChatModel(
        'deepseek-v4-flash',
        provider=OpenAIProvider(
            base_url='https://example.invalid/v1',
            api_key='test-key',
        ),
    )


def test_thinking_only_assistant_replay_uses_empty_content() -> None:
    mapped = _model()._map_model_response(
        ModelResponse(
            parts=[
                ThinkingPart(
                    content='done thinking, no final text',
                    id='reasoning_content',
                    provider_name='openai',
                )
            ]
        )
    )

    assert mapped is not None
    assert mapped['content'] == ''
    assert mapped.get('reasoning_content') == 'done thinking, no final text'
    assert 'tool_calls' not in mapped


def test_tool_call_assistant_may_keep_null_content() -> None:
    mapped = _model()._map_model_response(
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name='emit_comment',
                    args='{"paragraph_idx": 1, "comment": "x"}',
                    tool_call_id='call_1',
                )
            ]
        )
    )

    assert mapped is not None
    assert mapped['content'] is None
    assert mapped['tool_calls']


@pytest.mark.asyncio
async def test_retry_history_does_not_emit_invalid_assistant_message() -> None:
    """Regression for DeepSeek 400 after thinking-only tool-agent retries."""
    model = _model()
    messages = [
        ModelRequest(parts=[UserPromptPart(content='comment target paragraphs')]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name='emit_comment',
                    args='{"paragraph_idx": 1, "comment": "x"}',
                    tool_call_id='call_1',
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name='emit_comment',
                    content='accepted',
                    tool_call_id='call_1',
                )
            ]
        ),
        ModelResponse(
            parts=[
                ThinkingPart(
                    content='tool work complete',
                    id='reasoning_content',
                    provider_name='openai',
                )
            ]
        ),
        ModelRequest(parts=[RetryPromptPart(content='Please return text or call a tool.')]),
    ]

    mapped = await model._map_messages(messages, ModelRequestParameters())

    invalid_assistants = [
        item
        for item in mapped
        if item.get('role') == 'assistant'
        and item.get('content') in (None, '')
        and not item.get('tool_calls')
        and not item.get('reasoning_content')
    ]
    assert invalid_assistants == []

    thinking_only = [
        item
        for item in mapped
        if item.get('role') == 'assistant'
        and item.get('reasoning_content')
        and not item.get('tool_calls')
    ]
    assert len(thinking_only) == 1
    assert thinking_only[0]['content'] == ''
