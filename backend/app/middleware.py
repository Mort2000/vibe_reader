from __future__ import annotations

import logging
import time
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .observability import (
    annotate_current_span,
    new_request_id,
    new_trace_id,
    reset_request_context,
    set_request_context,
)

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        rid = headers.get("x-request-id") or new_request_id()
        tid = headers.get("x-trace-id") or new_trace_id()
        vrid = headers.get("x-verify-run-id", "")
        vsid = headers.get("x-verify-scenario-id", "")
        vstid = headers.get("x-verify-step-id", "")
        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))

        started = time.perf_counter()
        status_code = 500
        completed_logged = False

        tokens = set_request_context(
            request_id=rid,
            trace_id=tid,
            verify_run_id=vrid,
            verify_scenario_id=vsid,
            verify_step_id=vstid,
        )
        annotate_current_span(
            request_id=rid,
            trace_id=tid,
            verify_run_id=vrid,
            verify_scenario_id=vsid,
            verify_step_id=vstid,
        )

        def duration_ms() -> float:
            return round((time.perf_counter() - started) * 1000, 2)

        def fields(extra: dict[str, Any] | None = None) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms(),
            }
            if extra:
                payload.update(extra)
            return payload

        async def send_wrapper(message: Message) -> None:
            nonlocal completed_logged, status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 0) or 0)
                response_headers = MutableHeaders(scope=message)
                response_headers["x-request-id"] = rid
                response_headers["x-trace-id"] = tid

            await send(message)

            if message["type"] != "http.response.body":
                return
            if message.get("more_body", False) or completed_logged:
                return

            completed_logged = True
            logger.info(
                "http.request.completed",
                extra={
                    "event": "http.request.completed",
                    "fields": fields(),
                },
            )

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if not completed_logged:
                logger.exception(
                    "http.request.failed",
                    extra={
                        "event": "http.request.failed",
                        "fields": fields(),
                    },
                )
            raise
        finally:
            reset_request_context(tokens)
