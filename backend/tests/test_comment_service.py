from __future__ import annotations

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from app.services.comment_service import _usage_scope_from_messages


def test_usage_scope_detects_tool_call_without_successful_payload() -> None:
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="emit_comment",
                    args='{"paragraph_idx": 1, "comment": ""}',
                    tool_call_id="call_1",
                )
            ]
        )
    ]

    assert _usage_scope_from_messages(messages) == "run_aggregate"


def test_usage_scope_detects_single_text_response() -> None:
    messages = [ModelResponse(parts=[TextPart(content="no useful comment")])]

    assert _usage_scope_from_messages(messages) == "single_request"
