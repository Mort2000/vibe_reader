from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: dict | None = None,
    ):
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


ERROR_MAP = {
    "book_not_found": 404,
    "chapter_not_found": 404,
    "window_not_found": 404,
    "job_already_running": 409,
    "invalid_request": 400,
    "invalid_epub": 400,
    "invalid_progress": 400,
    "validation_error": 422,
    "internal_error": 500,
    "llm_provider_error": 502,
    "llm_timeout": 504,
    "llm_not_configured": 400,
    "verify_mode_required": 404,
    "data_dir_mismatch": 400,
    "unsafe_reset_target": 403,
}


def make_error_response(
    code: str,
    message: str,
    request_id: str | None = None,
    details: dict | None = None,
) -> tuple[dict, int]:
    status = ERROR_MAP.get(code, 500)
    body = {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        }
    }
    return body, status


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    from .observability import get_request_id

    rid = get_request_id()
    body, status = make_error_response(exc.code, exc.message, rid, exc.details)
    return JSONResponse(body, status_code=status)


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    from .observability import get_request_id

    rid = get_request_id()
    body, status = make_error_response("internal_error", str(exc), rid)
    return JSONResponse(body, status_code=status)
