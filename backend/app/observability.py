from __future__ import annotations

import contextvars
import contextlib
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from starlette.datastructures import Headers

from .config import Settings

_MANAGED_HANDLER_ATTR = "_vibe_reader_observability_handler"
_OTEL_RUNTIME: OtelRuntime | None = None
_METRIC_INSTRUMENTS: dict[str, Any] = {}

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
_span_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "span_id", default=""
)

RequestContextTokens = tuple[
    contextvars.Token[str],
    contextvars.Token[str],
    contextvars.Token[str],
    contextvars.Token[str],
    contextvars.Token[str],
    contextvars.Token[str],
]


@dataclass
class OtelRuntime:
    enabled: bool
    trace_provider: Any = None
    meter_provider: Any = None
    logger_provider: Any = None
    fastapi_instrumented: bool = False
    pydantic_ai_instrumented: bool = False
    warnings: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def force_flush(self) -> None:
        for provider in (
            self.logger_provider,
            self.meter_provider,
            self.trace_provider,
        ):
            if provider is None:
                continue
            flush = getattr(provider, "force_flush", None)
            if callable(flush):
                flush()

    def shutdown(self) -> None:
        for provider in (
            self.logger_provider,
            self.meter_provider,
            self.trace_provider,
        ):
            if provider is None:
                continue
            shutdown = getattr(provider, "shutdown", None)
            if callable(shutdown):
                shutdown()


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


def get_span_id() -> str:
    return _span_id_var.get()


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
    span_id: str = "",
) -> RequestContextTokens:
    return (
        _request_id_var.set(request_id or new_request_id()),
        _trace_id_var.set(trace_id or new_trace_id()),
        _verify_run_id_var.set(verify_run_id),
        _verify_scenario_id_var.set(verify_scenario_id),
        _verify_step_id_var.set(verify_step_id),
        _span_id_var.set(span_id),
    )


def reset_request_context(tokens: RequestContextTokens) -> None:
    (
        request_id_token,
        trace_id_token,
        verify_run_id_token,
        verify_scenario_id_token,
        verify_step_id_token,
        span_id_token,
    ) = tokens
    _request_id_var.reset(request_id_token)
    _trace_id_var.reset(trace_id_token)
    _verify_run_id_var.reset(verify_run_id_token)
    _verify_scenario_id_var.reset(verify_scenario_id_token)
    _verify_step_id_var.reset(verify_step_id_token)
    _span_id_var.reset(span_id_token)


def clear_request_context() -> None:
    _request_id_var.set("")
    _trace_id_var.set("")
    _verify_run_id_var.set("")
    _verify_scenario_id_var.set("")
    _verify_step_id_var.set("")
    _span_id_var.set("")


@contextlib.contextmanager
def request_context(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    verify_run_id: str = "",
    verify_scenario_id: str = "",
    verify_step_id: str = "",
    span_id: str = "",
):
    tokens = set_request_context(
        request_id=request_id,
        trace_id=trace_id,
        verify_run_id=verify_run_id,
        verify_scenario_id=verify_scenario_id,
        verify_step_id=verify_step_id,
        span_id=span_id,
    )
    try:
        yield
    finally:
        reset_request_context(tokens)


def _utc_timestamp(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _json_default(value: object) -> str:
    return str(value)


def _clean_attribute_value(value: Any) -> str | int | float | bool | None:
    if value is None:
        return None
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _clean_attributes(attrs: dict[str, Any] | None) -> dict[str, Any]:
    if not attrs:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in attrs.items():
        cleaned_value = _clean_attribute_value(value)
        if cleaned_value is not None:
            cleaned[key] = cleaned_value
    return cleaned


class _NoopSpan:
    def is_recording(self) -> bool:
        return False

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def record_exception(self, exception: Exception) -> None:
        return None

    def set_status(self, status: Any) -> None:
        return None


@contextlib.contextmanager
def _noop_span_context():
    yield _NoopSpan()


class _NoopTracer:
    def start_as_current_span(self, name: str):
        return _noop_span_context()


class _NoopInstrument:
    def add(self, amount: int, attributes: dict[str, Any] | None = None) -> None:
        return None

    def record(
        self,
        value: int | float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        return None


class _NoopMeter:
    def create_counter(
        self,
        name: str,
        *,
        unit: str = "",
        description: str = "",
    ) -> _NoopInstrument:
        return _NoopInstrument()

    def create_histogram(
        self,
        name: str,
        *,
        unit: str = "",
        description: str = "",
    ) -> _NoopInstrument:
        return _NoopInstrument()


def _safe_error_summary(exc: Exception) -> str:
    return type(exc).__name__


class LogContextFilter(logging.Filter):
    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        otel_trace_id, otel_span_id = current_otel_ids()
        if not hasattr(record, "event"):
            record.event = record.getMessage()
        if not hasattr(record, "fields"):
            record.fields = {}
        record.service = self._service
        record.environment = self._environment
        record.request_id = get_request_id()
        record.trace_id = get_trace_id()
        record.span_id = get_span_id() or otel_span_id
        record.otel_trace_id = otel_trace_id
        record.otel_span_id = otel_span_id
        record.verify_run_id = get_verify_run_id()
        record.verify_scenario_id = get_verify_scenario_id()
        record.verify_step_id = get_verify_step_id()
        return True


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": _utc_timestamp(record.created),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
            "logger": record.name,
            "service": getattr(record, "service", ""),
            "environment": getattr(record, "environment", ""),
            "request_id": getattr(record, "request_id", ""),
            "trace_id": getattr(record, "trace_id", ""),
            "span_id": getattr(record, "span_id", ""),
            "otel_trace_id": getattr(record, "otel_trace_id", ""),
            "otel_span_id": getattr(record, "otel_span_id", ""),
            "verify_run_id": getattr(record, "verify_run_id", ""),
            "verify_scenario_id": getattr(record, "verify_scenario_id", ""),
            "verify_step_id": getattr(record, "verify_step_id", ""),
        }
        fields = getattr(record, "fields", None)
        if fields:
            entry["fields"] = fields
        if record.exc_info:
            exc_type = record.exc_info[0]
            exc_value = record.exc_info[1]
            entry["exception"] = {
                "type": exc_type.__name__ if exc_type else "",
                "message": str(exc_value) if exc_value else "",
                "stacktrace": self.formatException(record.exc_info),
            }
        return json.dumps(entry, ensure_ascii=False, default=_json_default)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", record.getMessage())
        message = record.getMessage()
        parts = [
            _utc_timestamp(record.created),
            record.levelname,
            str(event),
        ]
        if message != event:
            parts.append(message)

        pairs = {
            "logger": record.name,
            "service": getattr(record, "service", ""),
            "env": getattr(record, "environment", ""),
            "request_id": getattr(record, "request_id", ""),
            "trace_id": getattr(record, "trace_id", ""),
            "span_id": getattr(record, "span_id", ""),
            "otel_trace_id": getattr(record, "otel_trace_id", ""),
            "otel_span_id": getattr(record, "otel_span_id", ""),
            "verify_run_id": getattr(record, "verify_run_id", ""),
            "verify_scenario_id": getattr(record, "verify_scenario_id", ""),
            "verify_step_id": getattr(record, "verify_step_id", ""),
        }
        for key, value in pairs.items():
            if value:
                parts.append(f"{key}={value}")

        fields = getattr(record, "fields", None)
        if fields:
            encoded_fields = json.dumps(
                fields,
                ensure_ascii=False,
                sort_keys=True,
                default=_json_default,
            )
            parts.append(f"fields={encoded_fields}")

        rendered = " ".join(parts)
        if record.exc_info:
            rendered = f"{rendered}\n{self.formatException(record.exc_info)}"
        return rendered


def _log_level(level_name: str) -> int:
    return getattr(logging, str(level_name).upper(), logging.INFO)


def _formatter(settings: Settings) -> logging.Formatter:
    if settings.observability.log_format == "text":
        return TextFormatter()
    return StructuredFormatter()


def _console_stream(settings: Settings):
    if settings.observability.console.stream == "stderr":
        return sys.stderr
    return sys.stdout


def _file_path(settings: Settings) -> Path:
    configured = settings.observability.file.path
    if not configured:
        return settings.logs_dir / "backend.jsonl"
    path = Path(configured).expanduser()
    if path.is_absolute():
        return path
    return settings.data_dir / path


def _add_handler(
    root: logging.Logger,
    handler: logging.Handler,
    *,
    level: int,
    formatter: logging.Formatter,
    log_filter: logging.Filter,
) -> None:
    setattr(handler, _MANAGED_HANDLER_ATTR, True)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(log_filter)
    root.addHandler(handler)


def _current_runtime_warnings() -> list[tuple[str, dict[str, Any]]]:
    if _OTEL_RUNTIME is None:
        return []
    return list(_OTEL_RUNTIME.warnings)


def _create_otel_log_handler() -> logging.Handler | None:
    if _OTEL_RUNTIME is None or _OTEL_RUNTIME.logger_provider is None:
        return None
    try:
        from opentelemetry.sdk._logs import LoggingHandler

        return LoggingHandler(logger_provider=_OTEL_RUNTIME.logger_provider)
    except Exception:
        return None


def _remove_managed_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if not getattr(handler, _MANAGED_HANDLER_ATTR, False):
            continue
        root.removeHandler(handler)
        handler.close()


def _sanitized_config(settings: Settings) -> dict[str, Any]:
    obs = settings.observability
    return {
        "enabled": obs.enabled,
        "service_name": obs.service_name,
        "environment": obs.environment,
        "log_level": obs.log_level,
        "log_format": obs.log_format,
        "log_sinks": obs.log_sinks,
        "console": {
            "enabled": obs.console.enabled,
            "stream": obs.console.stream,
        },
        "file": {
            "enabled": obs.file.enabled,
            "path_configured": bool(obs.file.path),
            "max_bytes": obs.file.max_bytes,
            "backup_count": obs.file.backup_count,
        },
        "otel": {
            "enabled": obs.otel.enabled,
            "endpoint_configured": bool(obs.otel.endpoint),
            "protocol": obs.otel.protocol,
            "export_traces": obs.otel.export_traces,
            "export_metrics": obs.otel.export_metrics,
            "export_logs": obs.otel.export_logs,
            "sample_ratio": obs.otel.sample_ratio,
        },
        "audit": {
            "enabled": obs.audit.enabled,
            "include_prompt_manifest": obs.audit.include_prompt_manifest,
            "include_full_prompt": obs.audit.include_full_prompt,
            "include_model_response": obs.audit.include_model_response,
            "redact_secrets": obs.audit.redact_secrets,
        },
        "verify_mode": settings.verify_mode,
    }


def _bounded_sample_ratio(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def _otel_signal_endpoint(endpoint: str, signal: str) -> str:
    base = endpoint.rstrip("/")
    if not base:
        return ""
    if base.endswith(f"/v1/{signal}"):
        return base
    if base.endswith("/v1"):
        return f"{base}/{signal}"
    return f"{base}/v1/{signal}"


def _otel_resource(settings: Settings) -> Any:
    from opentelemetry.sdk.resources import Resource

    return Resource.create({
        "service.name": settings.observability.service_name,
        "deployment.environment": settings.observability.environment,
    })


def _build_otel_runtime(settings: Settings) -> OtelRuntime:
    obs = settings.observability
    cfg = obs.otel
    if not obs.enabled or not cfg.enabled:
        return OtelRuntime(enabled=False)

    runtime = OtelRuntime(enabled=True)
    resource = _otel_resource(settings)
    endpoint = cfg.endpoint

    if not endpoint and (cfg.export_traces or cfg.export_metrics or cfg.export_logs):
        runtime.warnings.append(
            (
                "observability.otel_endpoint_missing",
                {
                    "export_traces": cfg.export_traces,
                    "export_metrics": cfg.export_metrics,
                    "export_logs": cfg.export_logs,
                },
            )
        )

    if cfg.protocol != "otlp_http":
        runtime.warnings.append(
            (
                "observability.otel_protocol_unsupported",
                {"protocol": cfg.protocol, "fallback": "otlp_http"},
            )
        )

    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        runtime.trace_provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(_bounded_sample_ratio(cfg.sample_ratio))),
        )
        if endpoint and cfg.export_traces:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            runtime.trace_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=_otel_signal_endpoint(endpoint, "traces")
                    )
                )
            )
    except Exception as exc:
        runtime.warnings.append(
            (
                "observability.otel_trace_init_failed",
                {"error": type(exc).__name__},
            )
        )
        runtime.trace_provider = None

    try:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        metric_readers = []
        if endpoint and cfg.export_metrics:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )

            metric_readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(
                        endpoint=_otel_signal_endpoint(endpoint, "metrics")
                    )
                )
            )
        runtime.meter_provider = MeterProvider(
            resource=resource,
            metric_readers=metric_readers,
        )
    except Exception as exc:
        runtime.warnings.append(
            (
                "observability.otel_metric_init_failed",
                {"error": type(exc).__name__},
            )
        )
        runtime.meter_provider = None

    try:
        if endpoint and cfg.export_logs:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                OTLPLogExporter,
            )
            from opentelemetry.sdk._logs import LoggerProvider
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

            runtime.logger_provider = LoggerProvider(resource=resource)
            runtime.logger_provider.add_log_record_processor(
                BatchLogRecordProcessor(
                    OTLPLogExporter(endpoint=_otel_signal_endpoint(endpoint, "logs"))
                )
            )
    except Exception as exc:
        runtime.warnings.append(
            (
                "observability.otel_log_init_failed",
                {"error": type(exc).__name__},
            )
        )
        runtime.logger_provider = None

    return runtime


def setup_otel(settings: Settings) -> OtelRuntime:
    global _OTEL_RUNTIME
    if _OTEL_RUNTIME is not None:
        return _OTEL_RUNTIME

    runtime = _build_otel_runtime(settings)
    if runtime.enabled:
        try:
            from opentelemetry import metrics, trace

            if runtime.trace_provider is not None:
                trace.set_tracer_provider(runtime.trace_provider)
            if runtime.meter_provider is not None:
                metrics.set_meter_provider(runtime.meter_provider)
            if runtime.logger_provider is not None:
                from opentelemetry import _logs

                _logs.set_logger_provider(runtime.logger_provider)
        except Exception as exc:
            runtime.warnings.append(
                (
                    "observability.otel_global_install_failed",
                    {"error": type(exc).__name__},
                )
            )
    _OTEL_RUNTIME = runtime
    return runtime


def instrument_fastapi_app(app: Any, settings: Settings) -> None:
    runtime = _OTEL_RUNTIME
    if runtime is None or not runtime.enabled:
        return
    if getattr(app.state, "_otel_fastapi_instrumented", False):
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=runtime.trace_provider,
            meter_provider=runtime.meter_provider,
            server_request_hook=_fastapi_server_request_hook,
            exclude_spans=["receive", "send"],
        )
        app.state._otel_fastapi_instrumented = True
        runtime.fastapi_instrumented = True
    except Exception as exc:
        runtime.warnings.append(
            (
                "observability.fastapi_instrumentation_failed",
                {"error": type(exc).__name__},
            )
        )


def instrument_pydantic_ai(settings: Settings) -> None:
    runtime = _OTEL_RUNTIME
    if runtime is None or not runtime.enabled or not settings.observability.otel.export_traces:
        return
    if runtime.pydantic_ai_instrumented:
        return
    try:
        from pydantic_ai import Agent
        from pydantic_ai.models.instrumented import InstrumentationSettings

        Agent.instrument_all(
            InstrumentationSettings(
                tracer_provider=runtime.trace_provider,
                meter_provider=runtime.meter_provider,
                logger_provider=runtime.logger_provider,
                include_content=False,
                include_binary_content=False,
            )
        )
        runtime.pydantic_ai_instrumented = True
    except Exception as exc:
        runtime.warnings.append(
            (
                "observability.pydantic_ai_instrumentation_failed",
                {"error": type(exc).__name__},
            )
        )


def shutdown_otel() -> None:
    if _OTEL_RUNTIME is not None:
        _OTEL_RUNTIME.force_flush()


def current_otel_ids() -> tuple[str, str]:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if not ctx.is_valid:
            return "", ""
        return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
    except Exception:
        return "", ""


@contextlib.contextmanager
def start_observable_span(name: str, attributes: dict[str, Any] | None = None):
    try:
        tracer = get_tracer()
        span_cm = tracer.start_as_current_span(name)
    except Exception:
        span_cm = _noop_span_context()
    stack = contextlib.ExitStack()
    try:
        span = stack.enter_context(span_cm)
    except Exception:
        stack.close()
        stack = contextlib.ExitStack()
        span = stack.enter_context(_noop_span_context())
    try:
        set_span_attributes(span, attributes)
        yield span
    finally:
        try:
            stack.close()
        except Exception:
            pass


def set_span_attributes(span: Any, attributes: dict[str, Any] | None) -> None:
    try:
        if span is None:
            return
        is_recording = getattr(span, "is_recording", None)
        if callable(is_recording) and not is_recording():
            return
        for key, value in _clean_attributes(attributes).items():
            span.set_attribute(key, value)
    except Exception:
        return


def mark_span_error(span: Any, exc: Exception, *, error_code: str = "") -> None:
    if span is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        safe_summary = _safe_error_summary(exc)
        span.set_status(Status(StatusCode.ERROR, safe_summary))
        add_event = getattr(span, "add_event", None)
        if callable(add_event):
            add_event(
                "exception",
                {
                    "exception.type": type(exc).__name__,
                    "exception.escaped": False,
                },
            )
        set_span_attributes(
            span,
            {
                "error.code": error_code or type(exc).__name__,
                "error.summary": safe_summary,
            },
        )
    except Exception:
        return


def _fastapi_server_request_hook(span: Any, scope: dict[str, Any]) -> None:
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    headers = Headers(scope=scope)
    attrs = {
        "request.id": headers.get("x-request-id", ""),
        "app.trace_id": headers.get("x-trace-id", ""),
        "verify.run_id": headers.get("x-verify-run-id", ""),
        "verify.scenario_id": headers.get("x-verify-scenario-id", ""),
        "verify.step_id": headers.get("x-verify-step-id", ""),
    }
    for key, value in attrs.items():
        if value:
            span.set_attribute(key, value)


def annotate_current_span(
    *,
    request_id: str = "",
    trace_id: str = "",
    verify_run_id: str = "",
    verify_scenario_id: str = "",
    verify_step_id: str = "",
) -> None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is None or not span.is_recording():
            return
        attrs = {
            "request.id": request_id,
            "app.trace_id": trace_id,
            "verify.run_id": verify_run_id,
            "verify.scenario_id": verify_scenario_id,
            "verify.step_id": verify_step_id,
        }
        for key, value in attrs.items():
            if value:
                span.set_attribute(key, value)
    except Exception:
        return


def get_tracer(name: str = "vibe_reader.backend") -> Any:
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception:
        return _NoopTracer()


def get_meter(name: str = "vibe_reader.backend") -> Any:
    try:
        from opentelemetry import metrics

        return metrics.get_meter(name)
    except Exception:
        return _NoopMeter()


def _metric_instrument(
    name: str,
    kind: str,
    *,
    unit: str = "",
    description: str = "",
) -> Any:
    instrument = _METRIC_INSTRUMENTS.get(name)
    if instrument is not None:
        return instrument
    try:
        meter = get_meter()
        if kind == "counter":
            instrument = meter.create_counter(name, unit=unit, description=description)
        elif kind == "histogram":
            instrument = meter.create_histogram(
                name, unit=unit, description=description
            )
        else:
            instrument = _NoopInstrument()
    except Exception:
        instrument = _NoopInstrument()
    _METRIC_INSTRUMENTS[name] = instrument
    return instrument


def _counter(
    name: str,
    amount: int = 1,
    attributes: dict[str, Any] | None = None,
) -> None:
    try:
        _metric_instrument(name, "counter").add(amount, _clean_attributes(attributes))
    except Exception:
        return


def _histogram(
    name: str,
    value: int | float,
    *,
    unit: str = "",
    attributes: dict[str, Any] | None = None,
) -> None:
    try:
        _metric_instrument(name, "histogram", unit=unit).record(
            value,
            _clean_attributes(attributes),
        )
    except Exception:
        return


def record_job_metric(
    *,
    job_type: str,
    status: str,
    duration_ms: float | None = None,
) -> None:
    attrs = {"job_type": job_type, "status": status}
    _counter("vibe_reader_jobs_total", attributes=attrs)
    if duration_ms is not None:
        _histogram(
            "vibe_reader_job_duration_ms",
            duration_ms,
            unit="ms",
            attributes=attrs,
        )


def record_context_build_metric(
    *,
    task_type: str,
    status: str,
    duration_ms: float,
    estimated_tokens: int | None = None,
    context_degraded: bool = False,
    preflight_triggered: bool = False,
    hard_triggered: bool = False,
) -> None:
    attrs = {
        "task_type": task_type,
        "status": status,
        "context_degraded": context_degraded,
        "preflight_triggered": preflight_triggered,
        "hard_triggered": hard_triggered,
    }
    _counter("vibe_reader_context_builds_total", attributes=attrs)
    _histogram(
        "vibe_reader_context_build_duration_ms",
        duration_ms,
        unit="ms",
        attributes=attrs,
    )
    if estimated_tokens is not None:
        _histogram(
            "vibe_reader_context_tokens",
            estimated_tokens,
            unit="tokens",
            attributes={"task_type": task_type, "status": status},
        )


def record_agent_metric(
    *,
    agent: str,
    model: str,
    status: str,
    duration_ms: float,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_input_tokens: int | None = None,
) -> None:
    attrs = {"agent": agent, "model": model, "status": status}
    _counter("vibe_reader_agent_runs_total", attributes=attrs)
    _histogram(
        "vibe_reader_agent_duration_ms",
        duration_ms,
        unit="ms",
        attributes=attrs,
    )
    for token_type, value in (
        ("input", input_tokens),
        ("output", output_tokens),
        ("cached_input", cached_input_tokens),
    ):
        if value is not None:
            _histogram(
                "vibe_reader_agent_tokens",
                value,
                unit="tokens",
                attributes={**attrs, "token_type": token_type},
            )


def record_chat_metric(
    *,
    status: str,
    total_ms: float,
    ttft_ms: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    attrs = {"status": status}
    _counter("vibe_reader_chat_streams_total", attributes=attrs)
    _histogram("vibe_reader_chat_duration_ms", total_ms, unit="ms", attributes=attrs)
    if ttft_ms is not None:
        _histogram("vibe_reader_chat_ttft_ms", ttft_ms, unit="ms", attributes=attrs)
    for token_type, value in (("input", input_tokens), ("output", output_tokens)):
        if value is not None:
            _histogram(
                "vibe_reader_chat_tokens",
                value,
                unit="tokens",
                attributes={**attrs, "token_type": token_type},
            )


def record_sse_event_metric(*, event: str, status: str, count: int = 1) -> None:
    _counter(
        "vibe_reader_sse_events_total",
        amount=count,
        attributes={"event": event, "status": status},
    )


def setup_logging(settings: Settings) -> None:
    level = _log_level(settings.observability.log_level)
    formatter = _formatter(settings)
    log_filter = LogContextFilter(
        service=settings.observability.service_name,
        environment=settings.observability.environment,
    )
    root = logging.getLogger()
    _remove_managed_handlers(root)
    root.setLevel(level)

    init_warnings: list[tuple[str, dict[str, Any]]] = []
    sinks = settings.observability.log_sinks or ["console"]
    managed_handler_count = 0

    if "console" in sinks and settings.observability.console.enabled:
        _add_handler(
            root,
            logging.StreamHandler(_console_stream(settings)),
            level=level,
            formatter=formatter,
            log_filter=log_filter,
        )
        managed_handler_count += 1

    if "file" in sinks and settings.observability.file.enabled:
        try:
            path = _file_path(settings)
            path.parent.mkdir(parents=True, exist_ok=True)
            _add_handler(
                root,
                RotatingFileHandler(
                    path,
                    maxBytes=settings.observability.file.max_bytes,
                    backupCount=settings.observability.file.backup_count,
                    encoding="utf-8",
                ),
                level=level,
                formatter=formatter,
                log_filter=log_filter,
            )
            managed_handler_count += 1
        except OSError as exc:
            init_warnings.append(
                (
                    "observability.file_sink_failed",
                    {"error": type(exc).__name__, "path_configured": True},
                )
            )

    if "otel" in sinks:
        handler = _create_otel_log_handler()
        if handler is None:
            init_warnings.append(
                (
                    "observability.otel_log_sink_unavailable",
                    {"reason": "otel log exporter is not initialized"},
                )
            )
        else:
            _add_handler(
                root,
                handler,
                level=level,
                formatter=formatter,
                log_filter=log_filter,
            )
            managed_handler_count += 1

    if managed_handler_count == 0:
        _add_handler(
            root,
            logging.StreamHandler(sys.stdout),
            level=level,
            formatter=formatter,
            log_filter=log_filter,
        )
        managed_handler_count += 1
        init_warnings.append(
            (
                "observability.console_fallback",
                {"reason": "no configured log sink was available"},
            )
        )

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(
        "observability.logging_configured",
        extra={
            "event": "observability.logging_configured",
            "fields": _sanitized_config(settings),
        },
    )
    for event, fields in [*_current_runtime_warnings(), *init_warnings]:
        logger.warning(event, extra={"event": event, "fields": fields})
