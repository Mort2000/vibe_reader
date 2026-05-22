from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .observability import new_request_id, new_trace_id, set_request_context


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        rid = request.headers.get("x-request-id") or new_request_id()
        tid = request.headers.get("x-trace-id") or new_trace_id()
        vrid = request.headers.get("x-verify-run-id", "")
        vsid = request.headers.get("x-verify-scenario-id", "")
        vstid = request.headers.get("x-verify-step-id", "")

        set_request_context(
            request_id=rid,
            trace_id=tid,
            verify_run_id=vrid,
            verify_scenario_id=vsid,
            verify_step_id=vstid,
        )

        response: Response = await call_next(request)
        response.headers["x-request-id"] = rid
        response.headers["x-trace-id"] = tid
        return response
