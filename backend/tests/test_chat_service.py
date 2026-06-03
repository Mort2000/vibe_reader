from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.chat_service import build_chat_context


@pytest.mark.asyncio
async def test_build_chat_context_updates_manifest_totals(monkeypatch) -> None:
    async def fake_get_book(db, book_id):  # noqa: ANN001
        return {"id": book_id, "title": "Book"}

    async def fake_get_chapter(db, book_id, chapter_idx):  # noqa: ANN001
        return {"idx": chapter_idx, "title": "Chapter"}

    async def fake_build_context(db, **kwargs):  # noqa: ANN001, ARG001
        return SimpleNamespace(
            prompt="prompt",
            context_hash="ctx",
            estimated_tokens=1000,
            prompt_manifest={
                "total_estimate": 1000,
                "safe_total_estimate": 900,
                "raw_total_estimate": 800,
                "components": [
                    {"name": "live_original_chunks", "tokens": 500},
                    {"name": "ephemeral_recent_chat", "tokens": 0},
                ],
            },
        )

    async def fake_list_turns(db, session_id, limit):  # noqa: ANN001, ARG001
        return (
            [
                {
                    "user_msg": "hello",
                    "ai_msg": "world",
                    "status": "done",
                }
            ],
            1,
        )

    monkeypatch.setattr("app.services.chat_service.book_repo.get_book", fake_get_book)
    monkeypatch.setattr(
        "app.services.chat_service.chapter_repo.get_chapter",
        fake_get_chapter,
    )
    monkeypatch.setattr("app.services.chat_service.build_context", fake_build_context)
    monkeypatch.setattr("app.services.chat_service.chat_repo.list_turns", fake_list_turns)

    result = await build_chat_context(
        object(),
        book_id=1,
        chapter_idx=1,
        paragraph_idx=12,
        session_id=99,
        settings=Settings(),
    )

    chat_component = next(
        c
        for c in result.prompt_manifest["components"]
        if c["name"] == "ephemeral_recent_chat"
    )
    assert chat_component["tokens"] > 0
    assert result.estimated_tokens == 1000 + chat_component["tokens"]
    assert result.prompt_manifest["total_estimate"] == result.estimated_tokens
    assert result.prompt_manifest["safe_total_estimate"] == 900 + chat_component["tokens"]
    assert result.prompt_manifest["raw_total_estimate"] == 800 + chat_component["tokens"]
