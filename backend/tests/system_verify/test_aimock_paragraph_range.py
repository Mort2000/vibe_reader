"""Unit tests for comment_target_paragraphs range notation parsing."""

from __future__ import annotations

from tests.system_verify.llm_stub.paragraph_range import (
    expand_comment_target_spec,
    parse_comment_target_paragraphs,
)


def test_single_paragraph() -> None:
    assert expand_comment_target_spec("42") == [42]


def test_inclusive_range() -> None:
    assert expand_comment_target_spec("383..=390") == list(range(383, 391))


def test_multiple_ranges() -> None:
    assert expand_comment_target_spec("10..=12, 20") == [10, 11, 12, 20]


def test_reversed_range_normalizes() -> None:
    assert expand_comment_target_spec("390..=383") == list(range(383, 391))


def test_parse_from_prompt_block() -> None:
    content = """
<CURRENT_TASK>
comment_target_paragraphs = [20..=22]
mode = comment
</CURRENT_TASK>
"""
    assert parse_comment_target_paragraphs(content) == [20, 21, 22]


def test_legacy_comma_list_still_parses() -> None:
    assert expand_comment_target_spec("20, 21, 22") == [20, 21, 22]
