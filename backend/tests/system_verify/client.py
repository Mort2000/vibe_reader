"""HTTP API client for accessing the backend under test.

Auto-injects X-Verify-* headers, records all requests/responses
to api_requests.ndjson, and provides typed methods for each endpoint.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .run import RunManager


class APIRecord:
    """A single recorded API request/response pair."""

    __slots__ = (
        "method",
        "url",
        "status_code",
        "request_headers_sanitized",
        "response_headers",
        "request_body_summary",
        "response_body_summary",
        "duration_ms",
        "error",
        "trace_id",
        "request_id",
        "verify_run_id",
        "verify_scenario_id",
        "verify_step_id",
        "created_at",
    )

    def __init__(
        self,
        method: str,
        url: str,
        status_code: int | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
        trace_id: str = "",
        request_id: str = "",
        verify_run_id: str = "",
        verify_scenario_id: str = "",
        verify_step_id: str = "",
    ):
        self.method = method
        self.url = url
        self.status_code = status_code
        self.request_headers_sanitized: dict[str, str] = {}
        self.response_headers: dict[str, str] = {}
        self.request_body_summary: Any = None
        self.response_body_summary: Any = None
        self.duration_ms = duration_ms
        self.error = error
        self.trace_id = trace_id
        self.request_id = request_id
        self.verify_run_id = verify_run_id
        self.verify_scenario_id = verify_scenario_id
        self.verify_step_id = verify_step_id
        self.created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "verify_run_id": self.verify_run_id,
            "verify_scenario_id": self.verify_scenario_id,
            "verify_step_id": self.verify_step_id,
            "request_body_summary": self.request_body_summary,
            "response_body_summary": self.response_body_summary,
            "created_at": self.created_at,
        }


class TargetClient:
    """Async HTTP client for the backend under test."""

    def __init__(
        self,
        base_url: str,
        run_manager: RunManager,
        verify_scenario_id: str = "",
        verify_step_id: str = "",
        timeout: float = 30.0,
        context: dict[str, Any] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.run_manager = run_manager
        self.verify_scenario_id = verify_scenario_id
        self.verify_step_id = verify_step_id
        self._context = context
        self._records: list[APIRecord] = []
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    def set_scenario(self, scenario_id: str, step_id: str = "") -> None:
        self.verify_scenario_id = scenario_id
        self.verify_step_id = step_id

    def _verify_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "X-Verify-Run-Id": self.run_manager.run_id,
        }
        if self.verify_scenario_id:
            headers["X-Verify-Scenario-Id"] = self.verify_scenario_id
        if self.verify_step_id:
            headers["X-Verify-Step-Id"] = self.verify_step_id
        return headers

    def _sanitize_headers(self, headers: dict) -> dict[str, str]:
        """Remove sensitive headers before recording."""
        out = {}
        skip = {"authorization", "cookie", "x-api-key"}
        for k, v in headers.items():
            if k.lower() in skip:
                out[k] = "***REDACTED***"
            else:
                out[k] = v
        return out

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        files: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        accept: str | None = None,
    ) -> tuple[httpx.Response, APIRecord]:
        url = f"{self.base_url}{path}"
        rec = APIRecord(
            method=method,
            url=url,
            verify_run_id=self.run_manager.run_id,
            verify_scenario_id=self.verify_scenario_id,
            verify_step_id=self.verify_step_id,
        )

        req_headers = {**self._verify_headers()}
        if accept:
            req_headers["Accept"] = accept
        if headers:
            req_headers.update(headers)
        rec.request_headers_sanitized = self._sanitize_headers(req_headers)

        if json_body:
            rec.request_body_summary = _summarize_body(json_body)

        start = time.monotonic()
        try:
            resp = await self._client.request(
                method,
                path,
                json=json_body if json_body is not None else None,
                files=files,
                params=params,
                headers=req_headers,
            )
            elapsed = (time.monotonic() - start) * 1000
            rec.status_code = resp.status_code
            rec.duration_ms = elapsed
            rec.response_headers = dict(resp.headers)
            rec.trace_id = resp.headers.get("x-trace-id", "")
            rec.request_id = resp.headers.get("x-request-id", "")

            try:
                body = resp.json()
                rec.response_body_summary = _summarize_response_body(body)
            except Exception:
                rec.response_body_summary = (
                    f"<non-json: {resp.headers.get('content-type', 'unknown')}>"
                )

        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            rec.duration_ms = elapsed
            rec.error = str(exc)
            raise
        finally:
            self._records.append(rec)
            if self._context is not None:
                self._context["last_api_record"] = rec
            self.run_manager.write_ndjson("api_requests.ndjson", [rec.to_dict()])

        return resp, rec

    # -- High-level API methods --

    async def health(self) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("GET", "/api/health")
        return resp.json(), rec

    async def runtime(self) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("GET", "/api/runtime")
        return resp.json(), rec

    async def verify_runtime(self) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("GET", "/api/verify/runtime")
        return resp.json(), rec

    async def verify_reset(self, confirm_data_dir: str) -> tuple[dict, APIRecord]:
        resp, rec = await self._request(
            "POST",
            "/api/verify/reset",
            json_body={"confirm_data_dir": confirm_data_dir},
        )
        return resp.json(), rec

    async def settings(self) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("GET", "/api/settings")
        return resp.json(), rec

    async def list_books(self, params: dict | None = None) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("GET", "/api/books", params=params)
        return resp.json(), rec

    async def get_book(self, book_id: int) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("GET", f"/api/books/{book_id}")
        return resp.json(), rec

    async def import_book(self, file_path: str | Path) -> tuple[dict, APIRecord]:
        path = Path(file_path)
        with open(path, "rb") as f:
            files = {"file": (path.name, f, "application/epub+zip")}
            resp, rec = await self._request("POST", "/api/books/import", files=files)
        return resp.json(), rec

    async def list_chapters(self, book_id: int) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("GET", f"/api/books/{book_id}/chapters")
        return resp.json(), rec

    async def get_chapter(
        self, book_id: int, chapter_idx: int
    ) -> tuple[dict, APIRecord]:
        resp, rec = await self._request(
            "GET", f"/api/books/{book_id}/chapters/{chapter_idx}"
        )
        return resp.json(), rec

    async def list_paragraphs(
        self,
        book_id: int,
        chapter_idx: int,
        params: dict | None = None,
    ) -> tuple[dict, APIRecord]:
        resp, rec = await self._request(
            "GET",
            f"/api/books/{book_id}/chapters/{chapter_idx}/paragraphs",
            params=params,
        )
        return resp.json(), rec

    async def get_progress(self, book_id: int) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("GET", f"/api/books/{book_id}/progress")
        return resp.json(), rec

    async def update_progress(
        self,
        book_id: int,
        chapter_idx: int,
        paragraph_idx: int,
        scroll_pct: float = 0.0,
    ) -> tuple[dict, APIRecord]:
        body = {
            "chapter_idx": chapter_idx,
            "paragraph_idx": paragraph_idx,
            "scroll_pct": scroll_pct,
        }
        resp, rec = await self._request(
            "PUT", f"/api/books/{book_id}/progress", json_body=body
        )
        return resp.json(), rec

    async def list_comments(
        self,
        book_id: int,
        chapter_idx: int,
        params: dict | None = None,
    ) -> tuple[dict, APIRecord]:
        resp, rec = await self._request(
            "GET",
            f"/api/books/{book_id}/chapters/{chapter_idx}/comments",
            params=params,
        )
        return resp.json(), rec

    async def get_current_window(
        self,
        book_id: int,
        chapter_idx: int,
        paragraph_idx: int | None = None,
    ) -> tuple[dict, APIRecord]:
        params = {}
        if paragraph_idx is not None:
            params["paragraph_idx"] = paragraph_idx
        resp, rec = await self._request(
            "GET",
            f"/api/books/{book_id}/chapters/{chapter_idx}/windows/current",
            params=params,
        )
        return resp.json(), rec

    async def verify_llm_ping(self) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("POST", "/api/verify/llm-ping")
        return resp.json(), rec

    async def verify_metrics(
        self, run_id: str, scenario_id: str | None = None
    ) -> tuple[dict, APIRecord]:
        params: dict[str, Any] = {"run_id": run_id}
        if scenario_id:
            params["scenario_id"] = scenario_id
        resp, rec = await self._request("GET", "/api/verify/metrics", params=params)
        return resp.json(), rec

    async def verify_jobs(self, params: dict | None = None) -> tuple[dict, APIRecord]:
        resp, rec = await self._request("GET", "/api/verify/jobs", params=params)
        return resp.json(), rec

    async def stream_chat(
        self,
        book_id: int,
        chapter_idx: int,
        paragraph_idx: int,
        user_msg: str,
        session_id: int | None = None,
    ) -> tuple[dict, APIRecord]:
        """Send a chat request, collect the full streamed response."""
        body: dict[str, Any] = {
            "book_id": book_id,
            "chapter_idx": chapter_idx,
            "paragraph_idx": paragraph_idx,
            "user_msg": user_msg,
        }
        if session_id is not None:
            body["session_id"] = session_id

        resp, rec = await self._request(
            "POST",
            "/api/chat/stream",
            json_body=body,
            accept="text/event-stream",
        )
        return resp.json(), rec

    @property
    def records(self) -> list[APIRecord]:
        return list(self._records)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> TargetClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


def _summarize_body(body: Any) -> Any:
    """Summarize request body for recording (truncate large text)."""
    if isinstance(body, dict):
        out = {}
        for k, v in body.items():
            if isinstance(v, str) and len(v) > 200:
                out[k] = v[:200] + "...(truncated)"
            else:
                out[k] = v
        return out
    return body


def _summarize_response_body(body: Any) -> Any:
    """Summarize response body - keep structure but truncate large text fields."""
    if isinstance(body, dict):
        if "error" in body:
            return body
        out = {}
        for k, v in body.items():
            if isinstance(v, str) and len(v) > 500:
                out[k] = v[:500] + "...(truncated)"
            elif isinstance(v, list) and len(v) > 10:
                out[k] = f"[{len(v)} items]"
            else:
                out[k] = v
        return out
    return body
