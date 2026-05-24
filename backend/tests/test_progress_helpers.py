from __future__ import annotations

from app.domain.models import BookContextState
from app.services.progress_helpers import (
    compare_reading_positions,
    detect_jump_type,
    is_reading_at_least,
)


def _state(**overrides: object) -> BookContextState:
    defaults = {
        "id": 1,
        "book_id": 1,
        "active_chapter_idx": 0,
        "reading_paragraph_idx": 10,
        "context_frontier_chapter_idx": 0,
        "context_frontier_paragraph_idx": 10,
    }
    defaults.update(overrides)
    return BookContextState(**defaults)  # type: ignore[arg-type]


def test_detect_jump_type_backward_by_chapter() -> None:
    state = _state(
        active_chapter_idx=2,
        reading_paragraph_idx=10,
        context_frontier_chapter_idx=2,
        context_frontier_paragraph_idx=10,
    )
    assert detect_jump_type(state, 1, 50) == "backward"


def test_detect_jump_type_forward_by_context_frontier() -> None:
    state = _state(
        active_chapter_idx=0,
        reading_paragraph_idx=10,
        context_frontier_chapter_idx=0,
        context_frontier_paragraph_idx=10,
    )
    assert detect_jump_type(state, 0, 20) == "forward"


def test_detect_jump_type_normal_at_frontier() -> None:
    state = _state(
        active_chapter_idx=0,
        reading_paragraph_idx=10,
        context_frontier_chapter_idx=0,
        context_frontier_paragraph_idx=15,
    )
    assert detect_jump_type(state, 0, 12) == "normal"


def test_compare_reading_positions() -> None:
    assert compare_reading_positions(1, 10, 2, 0) == -1
    assert compare_reading_positions(2, 0, 1, 10) == 1
    assert compare_reading_positions(3, 5, 3, 5) == 0
    assert compare_reading_positions(3, 8, 3, 5) == 1


def test_is_reading_at_least() -> None:
    assert is_reading_at_least(3, 200, ref_chapter_idx=3, ref_paragraph_idx=150)
    assert not is_reading_at_least(3, 150, ref_chapter_idx=3, ref_paragraph_idx=200)
