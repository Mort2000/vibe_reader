"""OpenAI-compatible model adapters for provider quirks."""

from __future__ import annotations

from dataclasses import dataclass

from openai.types import chat
from pydantic_ai.models.openai import OpenAIChatModel


class CompatibleOpenAIChatModel(OpenAIChatModel):
    """OpenAI chat model with assistant replay fixes for strict providers.

    DeepSeek rejects replayed assistant messages that have ``content=null`` and
    no ``tool_calls`` even when ``reasoning_content`` is present. pydantic-ai
    emits that shape after thinking-only tool-agent retries.
    """

    @dataclass
    class _MapModelResponseContext(OpenAIChatModel._MapModelResponseContext):
        def _into_message_param(self) -> chat.ChatCompletionAssistantMessageParam | None:
            if not self.texts and not self.thinkings and not self.tool_calls:
                return None
            message_param = chat.ChatCompletionAssistantMessageParam(role='assistant')
            if self.thinkings:
                for field_name, contents in self.thinkings.items():
                    message_param[field_name] = '\n\n'.join(contents)
            if self.texts:
                message_param['content'] = '\n\n'.join(self.texts)
            elif self.tool_calls:
                message_param['content'] = None
            else:
                message_param['content'] = ''
            if self.tool_calls:
                message_param['tool_calls'] = self.tool_calls
            return message_param
