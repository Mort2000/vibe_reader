from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

from .config import Settings

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)
_verify_run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "verify_run_id", default=""
)
_verify_scenario_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "verify_scenario_id", default=""
)
_verify_step_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "verify_step_id", default=""
)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def new_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex[:24]}"


def get_request_id() -> str:
    return _request_id_var.get()


def get_trace_id() -> str:
    return _trace_id_var.get()


def get_verify_run_id() -> str:
    return _verify_run_id_var.get()


def get_verify_scenario_id() -> str:
    return _verify_scenario_id_var.get()


def get_verify_step_id() -> str:
    return _verify_step_id_var.get()


def ensure_trace_id() -> str:
    """Return current trace_id, creating one if absent.

    Only for background jobs that lack HTTP request context.
    Request handlers should use get_trace_id() instead.
    """
    trace_id = get_trace_id()
    if not trace_id:
        trace_id = new_trace_id()
        _trace_id_var.set(trace_id)
    return trace_id


def set_request_context(
    request_id: str | None = None,
    trace_id: str | None = None,
    verify_run_id: str = "",
    verify_scenario_id: str = "",
    verify_step_id: str = "",
) -> None:
    _request_id_var.set(request_id or new_request_id())
    _trace_id_var.set(trace_id or new_trace_id())
    _verify_run_id_var.set(verify_run_id)
    _verify_scenario_id_var.set(verify_scenario_id)
    _verify_step_id_var.set(verify_step_id)


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "request_id": get_request_id(),
            "trace_id": get_trace_id(),
            "verify_run_id": get_verify_run_id(),
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            entry["fields"] = fields
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(settings: Settings) -> None:
    level = getattr(logging, settings.observability.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    if settings.observability.log_json:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
