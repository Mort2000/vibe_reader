"""Unit tests for chat audit exporter."""

from __future__ import annotations

import json
from pathlib import Path

from tests.system_verify.audit_exporter import (
    ChatAuditExporter,
    ChatSampleDraft,
    ensure_chat_audit_exporter,
)
from tests.system_verify.core.client_factory import ChatStreamResult
from tests.system_verify.core.config import VerifyConfig
from tests.system_verify.core.run_manager import RunManager
from tests.system_verify.flows.chat import ChatTurnRecord


def test_chat_audit_exporter_writes_ndjson_and_markdown(tmp_path: Path) -> None:
    config = VerifyConfig()
    run_manager = RunManager(config, run_id="20260530T120000Z_test0001")
    run_manager.base_dir = tmp_path
    run_manager.start()

    exporter = ChatAuditExporter(run_manager, config)
    turn = ChatTurnRecord(
        user_msg="这里为什么有点奇怪？",
        result=ChatStreamResult(
            user_msg="这里为什么有点奇怪？",
            session_id=3,
            turn_id=7,
            trace_id="trace_chat_7",
            ai_msg="stub answer",
            ttft_ms=100.0,
            total_ms=900.0,
            tokens_in=50,
            tokens_out=20,
            deltas=["stub"],
            events=[
                {"event_type": "chat.delta", "data": {"delta": "stub"}},
                {"event_type": "chat.done", "data": {"ai_msg": "stub answer"}},
            ],
        ),
        chapter_idx=1,
        paragraph_idx=24,
    )
    exporter.add_turns_from_records(
        [turn],
        scenario_id="S5_direct_chat",
        book={"id": 1, "title": "Test Book"},
        paragraphs=[{"paragraph_idx": 24, "text": "target paragraph"}],
        model="deepseek-v4-flash",
    )

    ndjson_count, md_count = exporter.export()
    assert ndjson_count == 1
    assert md_count == 1

    records = [
        json.loads(line)
        for line in (tmp_path / "audit" / "chats.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records[0]["sample_id"].startswith("chat_S5_direct_chat_")
    assert records[0]["user_msg"] == "这里为什么有点奇怪？"
    assert records[0]["ttft_ms"] == 100.0
    assert (tmp_path / "audit" / "samples" / f"{records[0]['sample_id']}.md").exists()


def test_chat_audit_exporter_followup_link() -> None:
    config = VerifyConfig()
    run_manager = RunManager(config, run_id="20260530T120000Z_test0002")
    exporter = ChatAuditExporter(run_manager, config)

    first_id = exporter.add_turn(
        ChatSampleDraft(
            scenario_id="S6_followup_chat",
            book={"id": 1, "title": "Test Book"},
            chapter_idx=1,
            paragraph_idx=24,
            session_id=1,
            turn_id=1,
            user_msg="q1",
            ai_msg="a1",
        ),
        turn_index=0,
    )
    second_id = exporter.add_turn(
        ChatSampleDraft(
            scenario_id="S6_followup_chat",
            book={"id": 1, "title": "Test Book"},
            chapter_idx=1,
            paragraph_idx=24,
            session_id=1,
            turn_id=2,
            user_msg="q2",
            ai_msg="a2",
        ),
        turn_index=1,
        followup_of_index=0,
    )
    assert first_id != second_id


def test_ensure_chat_audit_exporter_creates_exporter() -> None:
    from tests.system_verify.core.context import ScenarioContext
    from tests.system_verify.metrics_collector import MetricsAggregator

    config = VerifyConfig()
    run_manager = RunManager(config, run_id="20260530T120000Z_test0003")
    ctx = ScenarioContext(
        config=config,
        run_manager=run_manager,
        metrics=MetricsAggregator(run_manager, config),
        scenario_id="S5_direct_chat",
    )
    assert ctx.chat_audit_exporter is None
    exporter = ensure_chat_audit_exporter(ctx)
    assert isinstance(exporter, ChatAuditExporter)
    assert ctx.chat_audit_exporter is exporter
