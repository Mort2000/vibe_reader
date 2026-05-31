from __future__ import annotations

from vibe_verify.observability import agent_invocation_from_backend_row


def test_backend_agent_run_maps_tokens_cost_and_source() -> None:
    invocation = agent_invocation_from_backend_row(
        {
            "invocation_id": "inv-1",
            "agent_name": "Reader",
            "verify_run_id": "run",
            "trace_id": "trace",
            "input_tokens": 12,
            "output_tokens": 3,
            "cost_usd": 0.004,
            "interaction": {
                "usage": {"source": "provider"},
                "model": "real-model",
            },
        }
    )

    assert invocation.usage.input == 12
    assert invocation.usage.output == 3
    assert invocation.usage.total == 15
    assert invocation.usage.cost_usd == 0.004
    assert invocation.usage.source == "provider"
    assert invocation.usage.model == "real-model"


def test_backend_agent_run_preserves_zero_and_numeric_correlation_fields() -> None:
    invocation = agent_invocation_from_backend_row(
        {
            "invocation_id": "inv-1",
            "agent_name": "Reader",
            "verify_run_id": "run",
            "book_id": "7",
            "chapter_idx": 0,
            "job_id": "4",
            "interaction": {
                "book_id": 99,
                "chapter_idx": 3,
                "job_id": 8,
            },
        }
    )

    assert invocation.correlation.book_id == 7
    assert invocation.correlation.chapter_idx == 0
    assert invocation.correlation.job_id == 4
    assert invocation.usage.source == "estimate"
