"""Unit tests for compaction anchor excerpt normalization."""

from __future__ import annotations

from app.services.agent_base import ChapterCompressedSummaryOutput
from app.services.compaction_service import _normalize_anchor_excerpts


def test_normalize_anchor_excerpts_dict_without_reason_gets_default() -> None:
    paragraphs = [
        {"paragraph_idx": 63, "text": "好想把国家卖了逃之夭夭啊"},
        {"paragraph_idx": 81, "text": "说是超穷国也太夸张了吧"},
    ]
    normalized = _normalize_anchor_excerpts(
        [
            {"text": "好想把国家卖了逃之夭夭啊", "paragraph_idx": 63},
            {"text": "说是超穷国也太夸张了吧", "paragraph_idx": 81},
        ],
        chapter_idx=1,
        source_paragraphs=paragraphs,
    )
    assert len(normalized) == 2
    assert normalized[0]["reason"] == "anchor"
    assert normalized[1]["reason"] == "anchor"
    output = ChapterCompressedSummaryOutput.model_validate(
        {"summary": "测试摘要", "anchor_excerpts": normalized}
    )
    assert len(output.anchor_excerpts) == 2


def test_normalize_anchor_excerpts_preserves_explicit_reason() -> None:
    normalized = _normalize_anchor_excerpts(
        [
            {
                "text": "关键对白",
                "paragraph_idx": 10,
                "chapter_idx": 1,
                "reason": "体现主角性格",
            }
        ],
        chapter_idx=1,
        source_paragraphs=[{"paragraph_idx": 10, "text": "关键对白"}],
    )
    assert normalized[0]["reason"] == "体现主角性格"


def test_normalize_anchor_excerpts_string_still_works() -> None:
    normalized = _normalize_anchor_excerpts(
        ["“好想把国家卖了逃之夭夭啊──────！”"],
        chapter_idx=1,
        source_paragraphs=[],
    )
    assert normalized[0]["reason"] == "anchor"
    assert normalized[0]["text"].startswith("“好想把国家")
