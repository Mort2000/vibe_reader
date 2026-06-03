from __future__ import annotations

import pytest

from app.config import Settings
from app.domain.context_plan import ContextPlan, LiveOriginalChunkSelection
from app.services.context_builder import _apply_overflow


@pytest.mark.asyncio
async def test_apply_overflow_keeps_live_chunks_contiguous(monkeypatch) -> None:
    async def fake_get_latest_summary(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    async def fail_get_chunk(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("overflow must not inspect chunks for live dropping")

    monkeypatch.setattr(
        "app.services.context_builder.summary_repo.get_latest_summary",
        fake_get_latest_summary,
    )
    monkeypatch.setattr(
        "app.services.context_builder.chunk_repo.get_chunk",
        fail_get_chunk,
    )

    settings = Settings()
    settings.context_l3.compression_trigger_input_tokens = 1000
    settings.context_l3.allow_emergency_overflow_once = False
    plan = ContextPlan(
        chapter_idx=1,
        frontier=521,
        live_start=180,
        live_chunks=LiveOriginalChunkSelection(
            block_text="<LIVE_ORIGINAL_CHUNKS>...</LIVE_ORIGINAL_CHUNKS>",
            chunk_ids=[2, 3],
            estimated_tokens=6000,
        ),
    )

    new_plan, estimated_tokens, context_degraded, emergency_used = (
        await _apply_overflow(
            object(),
            1,
            1,
            plan,
            5000,
            settings,
            overflow_already_used=True,
            target_paragraphs=[441],
        )
    )

    assert new_plan.live_chunks.chunk_ids == [2, 3]
    assert estimated_tokens == 5000
    assert context_degraded is False
    assert emergency_used is False
