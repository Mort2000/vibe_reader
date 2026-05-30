"""Response contract validation against spec_interface.md definitions."""

from __future__ import annotations

from typing import Any

from ..core.client_factory import APIRecord


class ContractError(Exception):
    """Raised when a response violates the API contract."""

    def __init__(self, message: str, rec: APIRecord | None = None):
        super().__init__(message)
        self.record = rec


def validate_success(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate that a successful response does not contain an error body."""
    if isinstance(resp_body, dict) and "error" in resp_body:
        err = resp_body["error"]
        code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
        raise ContractError(f"Expected success but got error: {code}", rec)


def validate_error(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate error response structure per spec 3.2."""
    if "error" not in resp_body:
        raise ContractError("Error response missing 'error' key", rec)

    err = resp_body["error"]
    if not isinstance(err, dict):
        raise ContractError("Error field must be a dict", rec)

    for required in ("code", "message"):
        if required not in err:
            raise ContractError(f"Error response missing '{required}'", rec)

    if "request_id" not in err:
        raise ContractError("Error response missing 'request_id'", rec)

    # Check no API key leakage
    _check_no_api_key(resp_body, rec)


def validate_list_response(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate paginated list response: { items: [], total: int }."""
    if "items" not in resp_body:
        raise ContractError("List response missing 'items'", rec)
    if "total" not in resp_body:
        raise ContractError("List response missing 'total'", rec)
    if not isinstance(resp_body["items"], list):
        raise ContractError("'items' must be a list", rec)


def validate_health(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate health response: { status, time }."""
    if resp_body.get("status") != "ok":
        raise ContractError(f"Health status not 'ok': {resp_body.get('status')}", rec)
    if "time" not in resp_body:
        raise ContractError("Health response missing 'time'", rec)


def validate_runtime(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate runtime response per spec 5.2."""
    for field in ("app", "version", "verify_mode", "llm"):
        if field not in resp_body:
            raise ContractError(f"Runtime response missing '{field}'", rec)

    llm = resp_body.get("llm", {})
    if not isinstance(llm, dict):
        raise ContractError("Runtime 'llm' must be a dict", rec)

    # Must not expose api_key
    if "api_key" in llm and llm["api_key"]:
        raise ContractError("Runtime response must not expose api_key", rec)


def validate_import_response(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate book import response per spec 6.1."""
    if "book" not in resp_body:
        raise ContractError("Import response missing 'book'", rec)
    if "import_stats" not in resp_body:
        raise ContractError("Import response missing 'import_stats'", rec)

    book = resp_body["book"]
    for field in ("id", "title", "total_chapters"):
        if field not in book:
            raise ContractError(f"Import book missing '{field}'", rec)

    stats = resp_body["import_stats"]
    for field in ("chapter_count", "paragraph_count", "duration_ms"):
        if field not in stats:
            raise ContractError(f"Import stats missing '{field}'", rec)


def validate_progress_response(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate progress update response per spec 8.2."""
    if "progress" not in resp_body:
        raise ContractError("Progress response missing 'progress'", rec)

    validate_reading_progress(resp_body["progress"], rec, require_updated_at=True)


def validate_reading_progress(
    progress: dict,
    rec: APIRecord | None = None,
    *,
    require_updated_at: bool = False,
) -> None:
    """Validate a reading progress record from GET or nested PUT response."""
    for field in ("book_id", "chapter_idx", "paragraph_idx", "scroll_pct"):
        if field not in progress:
            raise ContractError(f"Reading progress missing '{field}'", rec)

    if require_updated_at and progress.get("updated_at") is None:
        raise ContractError("Reading progress missing 'updated_at'", rec)


def validate_chapters_response(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate chapter list response."""
    validate_list_response(resp_body, rec)
    if resp_body["items"]:
        ch = resp_body["items"][0]
        for field in ("book_id", "idx", "title", "paragraph_count"):
            if field not in ch:
                raise ContractError(f"Chapter item missing '{field}'", rec)


def validate_paragraphs_response(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate paragraph list response."""
    validate_list_response(resp_body, rec)
    if resp_body["items"]:
        p = resp_body["items"][0]
        for field in ("book_id", "chapter_idx", "paragraph_idx", "text"):
            if field not in p:
                raise ContractError(f"Paragraph item missing '{field}'", rec)


def validate_comments_response(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate chapter comments list response per spec 9.1."""
    for field in ("book_id", "chapter_idx", "items", "total"):
        if field not in resp_body:
            raise ContractError(f"Comments response missing '{field}'", rec)
    if not isinstance(resp_body["items"], list):
        raise ContractError("'items' must be a list", rec)

    for comment in resp_body["items"]:
        for required in (
            "id",
            "book_id",
            "chapter_idx",
            "paragraph_idx",
            "window_id",
            "comment",
            "comment_type",
            "status",
        ):
            if required not in comment:
                raise ContractError(f"Comment item missing '{required}'", rec)

    validate_no_span_in_comments(resp_body, rec)
    _check_no_api_key(resp_body, rec)


def validate_window_response(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Validate current window response per spec 9.2."""
    window = resp_body.get("window")
    if window is None:
        return
    for field in (
        "id",
        "book_id",
        "chapter_idx",
        "window_seq",
        "start_paragraph_idx",
        "end_paragraph_idx",
        "focus_start_paragraph_idx",
        "focus_end_paragraph_idx",
        "status",
    ):
        if field not in window:
            raise ContractError(f"Window missing '{field}'", rec)


def validate_no_span_in_comments(resp_body: dict, rec: APIRecord | None = None) -> None:
    """Ensure comments don't contain span fields per spec 9.1."""
    if "items" not in resp_body:
        return
    for c in resp_body["items"]:
        for forbidden in ("span_start", "span_end", "span"):
            if forbidden in c:
                raise ContractError(f"Comment must not contain '{forbidden}'", rec)


def _check_no_api_key(body: Any, rec: APIRecord | None = None) -> None:
    """Recursively check that no API key appears in the response body."""
    text = str(body).lower()
    suspicious = ("sk-", "api_key", "apikey")
    for s in suspicious:
        if s in text and "api_key_configured" not in text:
            raise ContractError(
                f"Potential API key leak detected containing '{s}'", rec
            )
