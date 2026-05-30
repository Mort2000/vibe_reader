"""Comment structure and business rule assertions.

Pure validation helpers — no HTTP calls, reading advance, or audit file I/O.
HTTP fetch/wait logic lives in ``flows.comments``.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.config import VerifyConfig
from ..core.scenario import StepAssertionError, assert_that
from ..sse_collector import SSEEvent

logger = logging.getLogger(__name__)


def window_is_no_call(
    window: dict[str, Any] | None, comments: list[dict[str, Any]]
) -> bool:
    if not window:
        return False
    if window.get("no_call") is True:
        return True
    if window.get("status") == "done" and not comments:
        ready = window.get("comments_ready_count")
        target = window.get("comments_target_count")
        if ready == 0 and target is not None:
            return True
    return False


def _validation_failure_summary(comment: dict[str, Any]) -> str:
    text = str(comment.get("comment") or comment.get("validation_error") or "")
    text = text.strip()
    if len(text) <= 120:
        return text
    return text[:120] + "…"


def collect_validation_failures(
    comments: list[dict[str, Any]],
    window: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect desensitized validation failure summaries for audit export."""
    failures: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()

    for comment in comments:
        if not comment.get("validation_failed"):
            continue
        key = (comment.get("paragraph_idx"), comment.get("trace_id"))
        if key in seen:
            continue
        seen.add(key)
        failures.append(
            {
                "paragraph_idx": comment.get("paragraph_idx"),
                "trace_id": comment.get("trace_id"),
                "reason": comment.get("validation_error")
                or comment.get("discard_reason")
                or "validation_failed",
                "summary": _validation_failure_summary(comment),
            }
        )

    telemetry = window.get("comment_telemetry") if window else None
    if isinstance(telemetry, dict):
        for item in telemetry.get("validation_failures") or []:
            if not isinstance(item, dict):
                continue
            key = (item.get("paragraph_idx"), item.get("trace_id"))
            if key in seen:
                continue
            seen.add(key)
            failures.append(item)

    return failures


def raise_window_failed(window: dict[str, Any]) -> None:
    error = window.get("error") or window.get("failure") or {}
    message = error.get("message") if isinstance(error, dict) else str(error)
    code = error.get("code") if isinstance(error, dict) else "window_failed"
    raise StepAssertionError(
        assertion="window_failed",
        message=f"Comment window failed: {code} — {message or 'no details'}",
        expected="window.done or no-call window.done",
        actual=window,
    )


def progress_update_was_deduped(
    first_body: dict[str, Any], second_body: dict[str, Any]
) -> bool:
    """Return True when an identical progress PUT was treated as a no-op."""
    if second_body.get("deduped") is True or second_body.get("dedup") is True:
        return True

    first_progress = first_body.get("progress") or {}
    second_progress = second_body.get("progress") or {}
    first_updated = first_progress.get("updated_at")
    second_updated = second_progress.get("updated_at")
    return bool(first_updated and second_updated and first_updated == second_updated)


def window_covers_paragraph(window: dict[str, Any] | None, paragraph_idx: int) -> bool:
    if not window:
        return False
    start = window.get("start_paragraph_idx")
    end = window.get("end_paragraph_idx")
    if start is None or end is None:
        return False
    return start <= paragraph_idx <= end


def assert_no_comment_recreated_events(
    new_comment_events: list[SSEEvent],
    comments_before: dict[int, int],
    chapter_idx: int,
) -> None:
    """Assert jump-back did not emit comment.created for already-covered paragraphs."""
    for evt in new_comment_events:
        paragraph_idx = evt.paragraph_idx or evt.data.get("paragraph_idx")
        if paragraph_idx is None:
            continue
        if int(paragraph_idx) in comments_before:
            raise StepAssertionError(
                assertion="comment_reuse",
                message=(
                    f"comment.created emitted for already-completed paragraph "
                    f"{paragraph_idx} in chapter {chapter_idx}"
                ),
                expected="reuse existing comment",
                actual=evt.to_dict(),
            )


def assert_comment_ids_stable(
    items: list[dict[str, Any]],
    comments_before: dict[int, int],
) -> None:
    """Assert persisted comment IDs remain stable after jump-back."""
    for paragraph_idx, comment_id in comments_before.items():
        current = next(
            (
                item
                for item in items
                if item.get("paragraph_idx") == paragraph_idx
            ),
            None,
        )
        if current is None:
            continue
        assert_that.equal(
            current.get("id"),
            comment_id,
            label=f"comment_id_stable_for_paragraph_{paragraph_idx}",
        )


def assert_comments_valid(
    comments: list[dict[str, Any]],
    *,
    window: dict[str, Any] | None = None,
    allow_no_call: bool = True,
    config: VerifyConfig | None = None,
) -> list[dict[str, Any]]:
    validation_failures = collect_validation_failures(comments, window)

    window_id = window.get("id") if window else None
    scoped_comments = (
        [c for c in comments if c.get("window_id") == window_id]
        if window_id is not None
        else comments
    )

    if not scoped_comments:
        if validation_failures:
            raise StepAssertionError(
                assertion="comment_validation_failed",
                message=(
                    "Window completed with validation failures but no persisted comments"
                ),
                expected="valid comments or explicit no-call window",
                actual={"window": window, "validation_failures": validation_failures},
            )
        if allow_no_call and window_is_no_call(window, scoped_comments):
            return validation_failures
        if window and window.get("status") == "done":
            if (
                config is not None
                and config.params.assertions.strict_done_without_comments
            ):
                raise StepAssertionError(
                    assertion="done_with_zero_comments",
                    message=(
                        "Param set requires comments or no_call for done windows "
                        "but found zero comments and no no_call marker"
                    ),
                    expected="persisted comments or explicit no-call window",
                    actual={"window": window, "comments": scoped_comments},
                )
            logger.warning(
                "Done window with zero comments and no no_call marker "
                "(scenario may need comment.telemetry or explicit no_call)"
            )
            return validation_failures
        raise StepAssertionError(
            assertion="comments_or_no_call",
            message="Expected persisted comments or a successful no-call window",
            actual={"window": window, "comments": scoped_comments},
        )

    focus_start = window.get("focus_start_paragraph_idx") if window else None
    focus_end = window.get("focus_end_paragraph_idx") if window else None

    for comment in scoped_comments:
        paragraph_idx = comment.get("paragraph_idx")
        assert_that.is_not_none(paragraph_idx, "comment.paragraph_idx")
        for forbidden in ("span_start", "span_end", "span"):
            assert_that.not_contains(
                comment,
                forbidden,
                label=f"comment[{paragraph_idx}] must not contain span fields",
            )
        if comment.get("validation_failed"):
            continue
        assert_that.is_true(
            bool(comment.get("comment", "").strip()) or comment.get("discarded"),
            f"comment[{paragraph_idx}] text must not be empty unless discarded",
        )
        if focus_start is not None and focus_end is not None:
            assert_that.is_true(
                focus_start <= paragraph_idx <= focus_end,
                f"comment paragraph {paragraph_idx} should be within focus range "
                f"[{focus_start}, {focus_end}]",
            )

    return validation_failures
