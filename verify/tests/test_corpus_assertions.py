from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from vibe_verify.assertions import (
    check_audit_invocation,
    check_available_count,
    check_chat_requests_without_selection,
    check_chat_response,
    check_chat_sse_contract,
    check_chat_usage,
    check_comments,
    check_compaction_summary_reused,
    check_followup_session,
    check_prompt_contains,
    check_response_status,
    check_sse_sequence,
    check_token_usage,
    extract_summary_text,
)
from vibe_verify.corpus import CorpusCatalog, CorpusRequirement
from vibe_verify.driver import ChatResponse
from vibe_verify.models import (
    AgentInvocation,
    APIInteraction,
    Correlation,
    SSEEvent,
    TokenUsage,
)


def write_manifest(tmp_path, *, sha256: str, license: str = "public-domain"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        f"""
[[books]]
alias = "book"
path = "book.epub"
license = "{license}"
sha256 = "{sha256}"

[[books.probes]]
name = "long"
purposes = ["core", "judge"]
min_context_tokens = 100
allow_real_llm = true
allow_external_judge = true
""",
        encoding="utf-8",
    )
    return manifest


def test_corpus_validate_resolve_and_export(tmp_path) -> None:
    digest = hashlib.sha256(b"epub").hexdigest()
    catalog = CorpusCatalog(write_manifest(tmp_path, sha256=digest))
    assert catalog.validate() == []
    resolved = catalog.resolve(
        CorpusRequirement("core", min_context_tokens=100, real_llm=True)
    )
    exported = catalog.export_resolved(tmp_path / "resolved.json", resolved)
    assert json.loads(exported.read_text())["probe"]["name"] == "long"


def test_corpus_validation_and_missing_probe(tmp_path) -> None:
    catalog = CorpusCatalog(write_manifest(tmp_path, sha256="wrong", license=""))
    errors = catalog.validate()
    assert any("license" in error for error in errors)
    assert any("sha256" in error for error in errors)

    digest = hashlib.sha256(b"epub").hexdigest()
    valid = CorpusCatalog(write_manifest(tmp_path / "other", sha256=digest))
    with pytest.raises(LookupError):
        valid.resolve(CorpusRequirement("missing"))


def invoke() -> AgentInvocation:
    return AgentInvocation(
        id="i",
        agent="Chat",
        prompt_messages=[{"role": "user", "content": "anchor"}],
        response={},
        usage=TokenUsage(input=2, output=1),
        correlation=Correlation(run_id="r"),
    )


def agent_invocation(
    agent: str,
    *,
    prompt: str = "",
    response=None,
) -> AgentInvocation:
    return AgentInvocation(
        id=agent,
        agent=agent,
        prompt_messages=[{"role": "user", "content": prompt or "prompt"}],
        response=response or {},
        usage=TokenUsage(input=2, output=1),
        correlation=Correlation(run_id="r"),
    )


def test_assertion_helpers_happy_path() -> None:
    check_response_status(200)
    check_sse_sequence(
        [
            SSEEvent("start", {}, Correlation("r")),
            SSEEvent("done", {}, Correlation("r")),
        ],
        ["start", "done"],
    )
    check_comments(
        [
            {
                "id": idx,
                "paragraph_idx": 2,
                "comment_type": comment_type,
                "comment": "ok",
            }
            for idx, comment_type in enumerate(
                ["observation", "question", "craft", "humor", "warning"], start=1
            )
        ],
        start=1,
        end=3,
    )
    check_chat_response(
        ChatResponse(
            text="answer",
            ttft_ms=1,
            duration_ms=2,
            tokens_in=3,
            tokens_out=2,
            usage_source="sse",
            events=[
                SSEEvent("chat.started", {}, Correlation("r")),
                SSEEvent("chat.delta", {}, Correlation("r")),
                SSEEvent("chat.done", {}, Correlation("r")),
            ],
        )
    )
    check_chat_usage(
        ChatResponse(
            text="answer",
            ttft_ms=1,
            duration_ms=2,
            tokens_in=3,
            tokens_out=2,
            usage_source="sse",
        )
    )
    check_chat_sse_contract(
        [
            SSEEvent("chat.started", {}, Correlation("r")),
            SSEEvent("chat.first_delta", {}, Correlation("r")),
            SSEEvent("chat.delta", {}, Correlation("r")),
            SSEEvent("chat.done", {}, Correlation("r")),
        ]
    )
    check_followup_session(
        ChatResponse(session_id=1, turn_id=1),
        ChatResponse(session_id=1, turn_id=2),
    )
    check_chat_requests_without_selection(
        [
            APIInteraction(
                method="POST",
                path="/api/chat/stream",
                status_code=200,
                duration_ms=1,
                correlation=Correlation("r"),
                request_body={
                    "keys": ["book_id", "chapter_idx", "paragraph_idx", "user_msg"]
                },
            )
        ]
    )
    check_prompt_contains(invoke(), "anchor")
    check_token_usage(
        TokenUsage(input=1, output=2), max_total=3, allowed_sources={"estimate"}
    )
    check_available_count("items", requested=1, available=1)
    check_audit_invocation(invoke())


def test_compaction_summary_reuse_checks_actual_summary_text() -> None:
    summary = "[stub:r1] rolling summary"
    compaction_response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "arguments": json.dumps(
                                    {"payload": {"summary": summary}},
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }
            }
        ]
    }
    check_compaction_summary_reused(
        [
            agent_invocation(
                "ContextCompactionAgent",
                response=compaction_response,
            ),
            agent_invocation(
                "ContextCompactionAgent",
                response={"choices": [{"message": {"content": "[stub] accepted"}}]},
            ),
            agent_invocation(
                "ReadingChatAgent",
                prompt=f"Use prior context: {summary}",
            ),
        ]
    )


def test_extract_summary_text_reads_compaction_v1_tool_events() -> None:
    summary = "纳特拉王国，国王病倒，维恩担任摄政。"
    raw_payload = json.dumps(
        {"payload": {"summary": summary, "anchor_excerpts": []}},
        ensure_ascii=False,
    )
    compaction_response = {
        "schema_version": "compaction_v1",
        "tool_events": [
            {
                "tool_name": "emit_chapter_compressed_summary",
                "arguments": {"payload": {"raw": raw_payload}},
            }
        ],
        "llm_rounds": [
            {
                "response": {
                    "tool_calls": [
                        {
                            "name": "emit_chapter_compressed_summary",
                            "arguments": {"raw": raw_payload},
                        }
                    ]
                }
            }
        ],
    }
    assert extract_summary_text(compaction_response) == summary
    check_compaction_summary_reused(
        [
            agent_invocation(
                "ContextCompactionAgent",
                response=compaction_response,
            ),
            agent_invocation(
                "ReadingChatAgent",
                prompt=f"<CHAPTER_COMPRESSED_SUMMARY>\n{summary}\n</CHAPTER_COMPRESSED_SUMMARY>",
            ),
        ]
    )


def test_compaction_summary_reuse_rejects_marker_only_prompt() -> None:
    with pytest.raises(AssertionError, match="not visible"):
        check_compaction_summary_reused(
            [
                agent_invocation(
                    "ContextCompactionAgent",
                    response={"summary": "actual compressed content"},
                ),
                agent_invocation(
                    "ReadingChatAgent",
                    prompt="ChapterCompressedSummary rolling summary marker only",
                ),
            ]
        )


@pytest.mark.parametrize(
    "call",
    [
        lambda: check_response_status(500),
        lambda: check_sse_sequence([], ["done"]),
        lambda: check_comments([], start=0, end=1),
        lambda: check_comments(
            [{"paragraph_idx": None, "comment_type": "observation", "comment": "ok"}],
            start=0,
            end=1,
        ),
        lambda: check_comments(
            [{"paragraph_idx": 1, "comment_type": "bad", "comment": "ok"}],
            start=0,
            end=1,
        ),
        lambda: check_chat_response(ChatResponse()),
        lambda: check_chat_usage(ChatResponse(tokens_in=1, tokens_out=1)),
        lambda: check_chat_usage(
            ChatResponse(tokens_in=0, tokens_out=0, usage_source="sse")
        ),
        lambda: check_chat_sse_contract(
            [
                SSEEvent("chat.started", {}, Correlation("r")),
                SSEEvent("chat.done", {}, Correlation("r")),
            ]
        ),
        lambda: check_chat_sse_contract(
            [
                SSEEvent("chat.delta", {}, Correlation("r")),
                SSEEvent("chat.started", {}, Correlation("r")),
                SSEEvent("chat.done", {}, Correlation("r")),
            ]
        ),
        lambda: check_followup_session(ChatResponse(session_id=1), ChatResponse()),
        lambda: check_followup_session(
            ChatResponse(session_id=1, turn_id=2),
            ChatResponse(session_id=1, turn_id=1),
        ),
        lambda: check_chat_requests_without_selection(
            [
                APIInteraction(
                    method="POST",
                    path="/api/chat/stream",
                    status_code=200,
                    duration_ms=1,
                    correlation=Correlation("r"),
                    request_body={"keys": ["book_id", "selection"]},
                )
            ]
        ),
        lambda: check_chat_requests_without_selection(
            [
                APIInteraction(
                    method="POST",
                    path="/api/chat/stream",
                    status_code=200,
                    duration_ms=1,
                    correlation=Correlation("r"),
                    request_body={
                        "keys": ["book_id", "anchor"],
                        "deep_keys": ["book_id", "anchor", "selectedText"],
                    },
                )
            ]
        ),
        lambda: check_chat_response(
            ChatResponse(
                text="answer",
                ttft_ms=2,
                duration_ms=1,
                events=[
                    SSEEvent("chat.started", {}, Correlation("r")),
                    SSEEvent("chat.delta", {}, Correlation("r")),
                    SSEEvent("chat.done", {}, Correlation("r")),
                ],
            )
        ),
        lambda: check_prompt_contains(invoke(), "missing"),
        lambda: check_token_usage(TokenUsage(input=2, output=2), max_total=3),
        lambda: check_available_count("items", requested=2, available=1),
    ],
)
def test_assertion_helpers_fail(call) -> None:
    with pytest.raises(AssertionError):
        call()


def test_assertions_are_not_disabled_by_python_optimize() -> None:
    code = (
        "from vibe_verify.assertions import check_response_status\n"
        "check_response_status(500)\n"
    )
    result = subprocess.run(
        [sys.executable, "-O", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unexpected HTTP status" in result.stderr
