from __future__ import annotations

import io
import json
import logging
import sys

import pytest

from app.config import Settings, load_settings
from app.infrastructure.audit import _apply_audit_config
from app.middleware import RequestContextMiddleware
from app.observability import (
    OtelRuntime,
    _build_otel_runtime,
    _fastapi_server_request_hook,
    _otel_signal_endpoint,
    clear_request_context,
    get_trace_id,
    instrument_pydantic_ai,
    mark_span_error,
    record_context_build_metric,
    request_context,
    setup_logging,
    shutdown_otel,
    start_observable_span,
)


OBS_ENV_KEYS = [
    "VIBE_READER_DATA_DIR",
    "VIBE_READER_OBSERVABILITY_ENABLED",
    "VIBE_READER_LOG_LEVEL",
    "VIBE_READER_LOG_FORMAT",
    "VIBE_READER_LOG_SINKS",
    "VIBE_READER_OTEL_ENDPOINT",
    "VIBE_READER_OTEL_ENABLED",
    "VIBE_READER_OTEL_EXPORT_TRACES",
    "VIBE_READER_OTEL_EXPORT_METRICS",
    "VIBE_READER_OTEL_EXPORT_LOGS",
    "VIBE_READER_OTEL_SAMPLE_RATIO",
    "VIBE_READER_VERIFY_MODE",
]


@pytest.fixture(autouse=True)
def restore_root_logging():
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    yield
    new_handlers = [handler for handler in root.handlers if handler not in old_handlers]
    for handler in new_handlers:
        handler.close()
    root.handlers.clear()
    for handler in old_handlers:
        root.addHandler(handler)
    root.setLevel(old_level)
    clear_request_context()


def _clear_obs_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in OBS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _settings(tmp_path, *, log_format: str = "json") -> Settings:
    settings = Settings(data_dir=tmp_path)
    settings.observability.service_name = "reader-test"
    settings.observability.environment = "test"
    settings.observability.log_level = "INFO"
    settings.observability.log_format = log_format
    settings.observability.log_json = log_format == "json"
    settings.observability.log_sinks = ["console", "file"]
    settings.observability.file.enabled = True
    settings.observability.file.path = str(tmp_path / "backend.log")
    return settings


def _flush_root_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_load_settings_observability_nested_config_and_env(monkeypatch, tmp_path) -> None:
    _clear_obs_env(monkeypatch)
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIBE_READER_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("VIBE_READER_LOG_SINKS", "console,file")
    monkeypatch.setenv("VIBE_READER_OTEL_ENABLED", "0")
    monkeypatch.setenv("VIBE_READER_OTEL_EXPORT_LOGS", "true")
    (tmp_path / "config.toml").write_text(
        """
[observability]
enabled = true
service_name = "reader-config"
environment = "ci"
log_format = "text"
log_sinks = ["console"]

[observability.console]
enabled = true
stream = "stderr"

[observability.file]
enabled = true
path = "logs/backend.jsonl"
max_bytes = 1234
backup_count = 2

[observability.otel]
enabled = true
endpoint = "https://collector.example/v1"
export_traces = true
export_metrics = false
export_logs = false
sample_ratio = 0.5

[observability.audit]
enabled = true
include_prompt_manifest = false
include_full_prompt = true
include_model_response = true
redact_secrets = false
""",
        encoding="utf-8",
    )

    settings = load_settings()

    obs = settings.observability
    assert obs.service_name == "reader-config"
    assert obs.environment == "ci"
    assert obs.log_level == "DEBUG"
    assert obs.log_format == "text"
    assert obs.log_json is False
    assert obs.log_sinks == ["console", "file"]
    assert obs.console.stream == "stderr"
    assert obs.file.enabled is True
    assert obs.file.path == "logs/backend.jsonl"
    assert obs.file.max_bytes == 1234
    assert obs.file.backup_count == 2
    assert obs.otel.enabled is False
    assert obs.otel.endpoint == "https://collector.example/v1"
    assert obs.otel.export_metrics is False
    assert obs.otel.export_logs is True
    assert obs.otel.sample_ratio == 0.5
    assert obs.audit.enabled is True
    assert obs.include_prompt_manifest is False
    assert obs.include_full_prompt is True
    assert obs.audit.include_model_response is True
    assert obs.audit.redact_secrets is False


def test_load_settings_nested_empty_otel_endpoint_overrides_legacy_root(
    monkeypatch,
    tmp_path,
) -> None:
    _clear_obs_env(monkeypatch)
    monkeypatch.setenv("VIBE_READER_DATA_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        """
[observability]
endpoint = "https://legacy.example/v1"

[observability.otel]
endpoint = ""
""",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.observability.otel.endpoint == ""
    assert settings.observability.otel_endpoint == ""


def test_setup_logging_routes_structured_logs_to_console_and_file(
    monkeypatch,
    tmp_path,
) -> None:
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    settings = _settings(tmp_path, log_format="json")

    setup_logging(settings)
    with request_context(
        request_id="req_test",
        trace_id="trace_test",
        verify_run_id="verify_run",
        verify_scenario_id="scenario",
        verify_step_id="step",
    ):
        logging.getLogger("tests.observability").info(
            "comment window completed",
            extra={
                "event": "comment_window.completed",
                "fields": {"job_id": 7, "window_id": 3},
            },
        )
    _flush_root_handlers()

    console_entries = [
        json.loads(line) for line in output.getvalue().splitlines() if line.strip()
    ]
    file_entries = [
        json.loads(line)
        for line in (tmp_path / "backend.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    console_event = next(
        item for item in console_entries if item["event"] == "comment_window.completed"
    )
    file_event = next(
        item for item in file_entries if item["event"] == "comment_window.completed"
    )

    assert console_event == file_event
    assert console_event["message"] == "comment window completed"
    assert console_event["service"] == "reader-test"
    assert console_event["environment"] == "test"
    assert console_event["request_id"] == "req_test"
    assert console_event["trace_id"] == "trace_test"
    assert console_event["verify_run_id"] == "verify_run"
    assert console_event["verify_scenario_id"] == "scenario"
    assert console_event["verify_step_id"] == "step"
    assert console_event["fields"] == {"job_id": 7, "window_id": 3}

    startup = next(
        item
        for item in console_entries
        if item["event"] == "observability.logging_configured"
    )
    assert startup["fields"]["otel"]["endpoint_configured"] is False


def test_setup_logging_reinitializes_and_closes_managed_file_handlers(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    settings = _settings(tmp_path, log_format="json")

    setup_logging(settings)
    first_file_handler = next(
        handler
        for handler in logging.getLogger().handlers
        if handler.__class__.__name__ == "RotatingFileHandler"
    )

    setup_logging(settings)

    assert first_file_handler.stream is None or first_file_handler.stream.closed


def test_setup_logging_preserves_caplog_handler(caplog, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    settings = _settings(tmp_path, log_format="json")
    settings.observability.log_sinks = ["console"]
    settings.observability.file.enabled = False

    with caplog.at_level(logging.INFO):
        setup_logging(settings)
        logging.getLogger("tests.observability").info(
            "captured by caplog",
            extra={"event": "observability.caplog_check"},
        )

    assert any(
        getattr(record, "event", "") == "observability.caplog_check"
        for record in caplog.records
    )


def test_setup_logging_text_format_is_readable(monkeypatch, tmp_path) -> None:
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    settings = _settings(tmp_path, log_format="text")
    settings.observability.log_sinks = ["console"]
    settings.observability.file.enabled = False

    setup_logging(settings)
    with request_context(request_id="req_readable", trace_id="trace_readable"):
        logging.getLogger("tests.observability").warning(
            "context budget degraded",
            extra={
                "event": "context.degraded",
                "fields": {"error_code": "context_budget_low"},
            },
        )
    _flush_root_handlers()

    rendered = output.getvalue()
    assert "context.degraded" in rendered
    assert "context budget degraded" in rendered
    assert "request_id=req_readable" in rendered
    assert "trace_id=trace_readable" in rendered
    assert '"error_code": "context_budget_low"' in rendered


@pytest.mark.asyncio
async def test_request_context_middleware_logs_after_final_body(caplog) -> None:
    messages = []

    async def app(scope, receive, send) -> None:  # noqa: ANN001
        assert get_trace_id() == "trace_in"
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [],
        })
        await send({
            "type": "http.response.body",
            "body": b"part1",
            "more_body": True,
        })
        assert not any(
            getattr(record, "event", "") == "http.request.completed"
            for record in caplog.records
        )
        await send({
            "type": "http.response.body",
            "body": b"part2",
            "more_body": False,
        })

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    middleware = RequestContextMiddleware(app)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/events",
        "headers": [
            (b"x-request-id", b"req_in"),
            (b"x-trace-id", b"trace_in"),
        ],
    }

    with caplog.at_level(logging.INFO):
        await middleware(scope, receive, send)  # type: ignore[arg-type]

    start_message = messages[0]
    response_headers = dict(start_message["headers"])
    assert response_headers[b"x-request-id"] == b"req_in"
    assert response_headers[b"x-trace-id"] == b"trace_in"
    assert get_trace_id() == ""

    completed = next(
        record
        for record in caplog.records
        if getattr(record, "event", "") == "http.request.completed"
    )
    assert completed.fields["method"] == "GET"
    assert completed.fields["path"] == "/api/events"
    assert completed.fields["status_code"] == 200


@pytest.mark.asyncio
async def test_request_context_middleware_logs_failure_and_resets(caplog) -> None:
    async def app(scope, receive, send) -> None:  # noqa: ANN001
        assert get_trace_id()
        raise RuntimeError("boom")

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        pass

    middleware = RequestContextMiddleware(app)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/books",
        "headers": [],
    }

    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        await middleware(scope, receive, send)  # type: ignore[arg-type]

    failed = next(
        record
        for record in caplog.records
        if getattr(record, "event", "") == "http.request.failed"
    )
    assert failed.fields["method"] == "POST"
    assert failed.fields["path"] == "/api/books"
    assert get_trace_id() == ""


@pytest.mark.asyncio
async def test_request_context_middleware_writes_generated_ids_to_current_span(
    monkeypatch,
) -> None:
    class FakeSpan:
        def __init__(self) -> None:
            self.attrs: dict[str, str] = {}

        def is_recording(self) -> bool:
            return True

        def set_attribute(self, key: str, value: str) -> None:
            self.attrs[key] = value

    fake_span = FakeSpan()
    from opentelemetry import trace

    monkeypatch.setattr(trace, "get_current_span", lambda: fake_span)

    async def app(scope, receive, send) -> None:  # noqa: ANN001
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        pass

    middleware = RequestContextMiddleware(app)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/health",
        "headers": [],
    }

    await middleware(scope, receive, send)  # type: ignore[arg-type]

    assert fake_span.attrs["request.id"].startswith("req_")
    assert fake_span.attrs["app.trace_id"].startswith("trace_")


def test_apply_audit_config_redacts_disabled_prompt_manifest_and_model_content() -> None:
    settings = Settings()
    settings.observability.audit.include_prompt_manifest = False
    settings.observability.audit.include_full_prompt = False
    settings.observability.audit.include_model_response = False
    packet = {
        "context_hash": "sha256:ctx",
        "prompt_manifest": {"components": [{"name": "live_original_chunks"}]},
        "prompt_messages": [{"role": "user", "content": "full prompt"}],
        "injected_context": {
            "builder": "ContextBuilder",
            "context_hash": "sha256:ctx",
            "components": [
                {
                    "name": "live_original_chunks",
                    "tokens": 100,
                    "content": {"id": 1, "text": "original body", "hash": "h"},
                }
            ],
        },
        "llm_rounds": [
            {
                "response": {
                    "content": "model response",
                    "thinking": {"content": "private"},
                    "tool_calls": [{"arguments": {"comment": "draft"}}],
                }
            }
        ],
        "tool_events": [
            {
                "arguments": {"payload": {"comment": "draft"}},
                "tool_result": {"content": "accepted"},
            }
        ],
        "final_result": {
            "comments_created": [{"text": "draft"}],
            "summary": "summary body",
            "anchor_excerpts": ["excerpt"],
            "ai_msg": "answer",
            "user_msg": "question",
        },
        "user_msg": "question",
    }

    redacted = _apply_audit_config(packet, settings)

    assert redacted["prompt_manifest"]["redacted"] is True
    assert redacted["prompt_messages"][0]["content"]["redacted"] is True
    assert redacted["injected_context"]["components"][0]["content"] == {
        "id": 1,
        "hash": "h",
    }
    assert "original body" not in json.dumps(redacted, ensure_ascii=False)
    assert "model response" not in json.dumps(redacted, ensure_ascii=False)
    assert redacted["llm_rounds"][0]["response"]["content_redacted"] is True
    assert redacted["tool_events"][0]["arguments"]["redacted"] is True
    assert redacted["final_result"]["comments_created"][0]["text_redacted"] is True
    assert redacted["final_result"]["summary_redacted"] is True
    assert redacted["final_result"]["anchor_excerpts_redacted"] is True


def test_otel_signal_endpoint_normalizes_collector_base_url() -> None:
    assert (
        _otel_signal_endpoint("http://collector:4318", "traces")
        == "http://collector:4318/v1/traces"
    )
    assert (
        _otel_signal_endpoint("http://collector:4318/v1", "metrics")
        == "http://collector:4318/v1/metrics"
    )
    assert (
        _otel_signal_endpoint("http://collector:4318/v1/logs", "logs")
        == "http://collector:4318/v1/logs"
    )


def test_build_otel_runtime_constructs_http_export_providers(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    settings.observability.otel.enabled = True
    settings.observability.otel.endpoint = "http://127.0.0.1:4318"
    settings.observability.otel.export_traces = True
    settings.observability.otel.export_metrics = True
    settings.observability.otel.export_logs = True

    runtime = _build_otel_runtime(settings)
    try:
        assert runtime.enabled is True
        assert runtime.trace_provider is not None
        assert runtime.meter_provider is not None
        assert runtime.logger_provider is not None
        assert runtime.warnings == []
    finally:
        runtime.shutdown()


def test_build_otel_runtime_warns_when_endpoint_missing(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    settings.observability.otel.enabled = True
    settings.observability.otel.endpoint = ""
    settings.observability.otel.export_logs = True

    runtime = _build_otel_runtime(settings)
    try:
        assert runtime.enabled is True
        assert runtime.trace_provider is not None
        assert runtime.meter_provider is not None
        assert runtime.logger_provider is None
        assert any(
            event == "observability.otel_endpoint_missing"
            for event, _fields in runtime.warnings
        )
    finally:
        runtime.shutdown()


def test_fastapi_server_request_hook_adds_correlation_attributes() -> None:
    class FakeSpan:
        def __init__(self) -> None:
            self.attrs: dict[str, str] = {}

        def is_recording(self) -> bool:
            return True

        def set_attribute(self, key: str, value: str) -> None:
            self.attrs[key] = value

    span = FakeSpan()
    scope = {
        "headers": [
            (b"x-request-id", b"req_hook"),
            (b"x-trace-id", b"trace_hook"),
            (b"x-verify-run-id", b"verify_hook"),
        ]
    }

    _fastapi_server_request_hook(span, scope)

    assert span.attrs["request.id"] == "req_hook"
    assert span.attrs["app.trace_id"] == "trace_hook"
    assert span.attrs["verify.run_id"] == "verify_hook"


def test_pydantic_ai_instrumentation_disables_content_by_default(
    monkeypatch,
    tmp_path,
) -> None:
    import app.observability as observability
    from pydantic_ai import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    settings = Settings(data_dir=tmp_path)
    settings.observability.otel.enabled = True
    settings.observability.otel.export_traces = True
    settings.observability.otel.export_metrics = True
    settings.observability.otel.export_logs = False
    runtime = _build_otel_runtime(settings)
    captured: dict[str, InstrumentationSettings] = {}

    def fake_instrument_all(instrument: InstrumentationSettings | bool = True) -> None:
        assert isinstance(instrument, InstrumentationSettings)
        captured["instrument"] = instrument

    monkeypatch.setattr(observability, "_OTEL_RUNTIME", runtime)
    monkeypatch.setattr(Agent, "instrument_all", fake_instrument_all)
    try:
        instrument_pydantic_ai(settings)
    finally:
        monkeypatch.setattr(observability, "_OTEL_RUNTIME", None)
        runtime.shutdown()

    instrument = captured["instrument"]
    assert instrument.include_content is False
    assert instrument.include_binary_content is False
    assert instrument.tracer is not None
    assert runtime.pydantic_ai_instrumented is True


def test_shutdown_otel_flushes_without_closing_global_runtime(monkeypatch) -> None:
    import app.observability as observability

    class FakeProvider:
        def __init__(self) -> None:
            self.flush_count = 0
            self.shutdown_count = 0

        def force_flush(self) -> None:
            self.flush_count += 1

        def shutdown(self) -> None:
            self.shutdown_count += 1

    provider = FakeProvider()
    runtime = OtelRuntime(enabled=True, trace_provider=provider)
    monkeypatch.setattr(observability, "_OTEL_RUNTIME", runtime)

    shutdown_otel()
    shutdown_otel()

    assert provider.flush_count == 2
    assert provider.shutdown_count == 0
    assert observability._OTEL_RUNTIME is runtime


def test_observability_facade_is_noop_when_tracer_or_meter_breaks(monkeypatch) -> None:
    import app.observability as observability

    class BrokenTracer:
        def start_as_current_span(self, name: str):  # noqa: ARG002
            raise RuntimeError("collector unavailable")

    class BrokenMeter:
        def create_counter(self, name: str, **kwargs):  # noqa: ANN003, ARG002
            raise RuntimeError("meter unavailable")

    monkeypatch.setattr(observability, "get_tracer", lambda name="": BrokenTracer())
    monkeypatch.setattr(observability, "get_meter", lambda name="": BrokenMeter())
    observability._METRIC_INSTRUMENTS.clear()

    with start_observable_span("service.test") as span:
        assert span.is_recording() is False

    record_context_build_metric(
        task_type="comment",
        status="ok",
        duration_ms=1.0,
        estimated_tokens=12,
    )


def test_mark_span_error_uses_safe_summary_without_exception_text() -> None:
    class FakeSpan:
        def __init__(self) -> None:
            self.attrs: dict[str, str] = {}
            self.events: list[tuple[str, dict]] = []
            self.record_exception_called = False
            self.status = None

        def is_recording(self) -> bool:
            return True

        def record_exception(self, exception: Exception) -> None:
            self.record_exception_called = True

        def set_status(self, status) -> None:  # noqa: ANN001
            self.status = status

        def set_attribute(self, key: str, value: str) -> None:
            self.attrs[key] = value

        def add_event(self, name: str, attributes: dict) -> None:
            self.events.append((name, attributes))

    span = FakeSpan()
    mark_span_error(
        span,
        RuntimeError("prompt=secret original body model output"),
        error_code="agent_failed",
    )

    assert span.attrs["error.code"] == "agent_failed"
    assert span.attrs["error.summary"] == "RuntimeError"
    assert span.record_exception_called is False
    assert span.events == [
        (
            "exception",
            {"exception.type": "RuntimeError", "exception.escaped": False},
        )
    ]
    assert "secret" not in str(span.status)
    assert "secret" not in json.dumps(span.events)


@pytest.mark.asyncio
async def test_sse_publisher_injects_current_observability_context(monkeypatch) -> None:
    from app.infrastructure import events as event_module

    recorded: list[dict[str, str | int]] = []

    def fake_record_sse_event_metric(
        *,
        event: str,
        status: str,
        count: int = 1,
    ) -> None:
        recorded.append({"event": event, "status": status, "count": count})

    monkeypatch.setattr(
        event_module,
        "record_sse_event_metric",
        fake_record_sse_event_metric,
    )
    publisher = event_module.SSEEventPublisher()
    queue = publisher.subscribe()

    with request_context(
        request_id="req_sse",
        trace_id="trace_sse",
        verify_run_id="verify_sse",
    ):
        await publisher.publish("window.running", {"job_id": 3, "trace_id": ""})

    evt = queue.get_nowait()
    assert evt["trace_id"] == "trace_sse"
    assert evt["request_id"] == "req_sse"
    assert evt["verify_run_id"] == "verify_sse"
    assert recorded == [
        {"event": "window.running", "status": "delivered", "count": 1}
    ]


@pytest.mark.asyncio
async def test_sse_publisher_aggregates_queue_full_metrics(monkeypatch, caplog) -> None:
    from app.infrastructure import events as event_module

    recorded: list[dict[str, str | int]] = []

    def fake_record_sse_event_metric(
        *,
        event: str,
        status: str,
        count: int = 1,
    ) -> None:
        recorded.append({"event": event, "status": status, "count": count})

    monkeypatch.setattr(
        event_module,
        "record_sse_event_metric",
        fake_record_sse_event_metric,
    )
    publisher = event_module.SSEEventPublisher()
    queue = publisher.subscribe()
    for idx in range(200):
        queue.put_nowait({"idx": idx})

    with caplog.at_level(logging.WARNING):
        await publisher.publish("window.running", {"job_id": 3})

    assert recorded == [{"event": "window.running", "status": "dropped", "count": 1}]
    warnings = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "sse.queue_full"
    ]
    assert len(warnings) == 1
    assert warnings[0].fields["dropped_count"] == 1


@pytest.mark.asyncio
async def test_job_submit_uses_same_trace_for_row_and_queued_event(tmp_path) -> None:
    from app.db import init_db
    from app.infrastructure.settings import SettingsProvider
    from app.repos import jobs as job_repo
    from app.services.job_runner import JobRunner

    class FakePublisher:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        async def publish(self, event: str, data: dict) -> None:
            self.events.append((event, data))

    db = await init_db(tmp_path / "jobs.db")
    now = "2026-05-30T00:00:00Z"
    await db.execute(
        """INSERT INTO books
           (id, title, author, file_hash, file_path, total_chapters, imported_at, updated_at)
           VALUES (1, 'book', 'author', 'hash', '/tmp/book.epub', 1, ?, ?)""",
        (now, now),
    )
    await db.execute(
        """INSERT INTO chapters
           (book_id, idx, title, raw_text, paragraph_count, token_estimate, created_at, updated_at)
           VALUES (1, 1, 'chapter', '', 1, 1, ?, ?)""",
        (now, now),
    )
    await db.execute(
        """INSERT INTO reading_windows
           (id, book_id, chapter_idx, window_seq, start_paragraph_idx, end_paragraph_idx,
            focus_start_paragraph_idx, focus_end_paragraph_idx,
            assistant_frontier_paragraph_idx, text_hash, context_hash, status,
            created_at, updated_at)
           VALUES (7, 1, 1, 1, 0, 0, 0, 0, 0, 'text', 'ctx', 'pending', ?, ?)""",
        (now, now),
    )
    await db.commit()
    publisher = FakePublisher()
    runner = JobRunner(
        SettingsProvider(Settings()),
        event_publisher=publisher,
    )

    try:
        job = await runner.submit_job(db, "comment_window", 1, 1, window_id=7)
        stored = await job_repo.get_job(db, job["id"])

        assert job["trace_id"].startswith("trace_")
        assert stored is not None
        assert stored["trace_id"] == job["trace_id"]
        assert publisher.events == [
            (
                "window.queued",
                {
                    "book_id": 1,
                    "chapter_idx": 1,
                    "window_id": 7,
                    "job_id": job["id"],
                    "job_type": "comment_window",
                    "trace_id": job["trace_id"],
                },
            )
        ]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_compaction_agent_missing_output_records_error_metric(monkeypatch) -> None:
    from app.domain.models import OriginalTextChunk
    from app.services import compaction_service

    class FakeAgent:
        async def run(self, prompt, *, deps, metadata):  # noqa: ANN001, ARG002
            return object()

    recorded: list[dict] = []

    def fake_record_agent_metric(**kwargs) -> None:  # noqa: ANN003
        recorded.append(kwargs)

    async def fake_paragraphs_range(db, book_id, chapter_idx, start, end):  # noqa: ANN001, ARG001
        return [{"paragraph_idx": 0, "text": "source text"}]

    monkeypatch.setattr(
        compaction_service,
        "get_compaction_agent",
        lambda settings: FakeAgent(),
    )
    monkeypatch.setattr(
        compaction_service.paragraph_repo,
        "get_paragraphs_range",
        fake_paragraphs_range,
    )
    monkeypatch.setattr(
        compaction_service,
        "record_agent_metric",
        fake_record_agent_metric,
    )

    with pytest.raises(ValueError):
        await compaction_service._run_compaction_llm(
            object(),
            book_id=1,
            chapter_idx=1,
            job_id=3,
            source_chunk=OriginalTextChunk(
                id=9,
                book_id=1,
                chapter_idx=1,
                chunk_seq=1,
                start_paragraph_idx=0,
                end_paragraph_idx=0,
                token_estimate=10,
            ),
            previous_summary_row=None,
            settings=Settings(),
        )

    assert recorded
    assert recorded[-1]["agent"] == "ContextCompactionAgent"
    assert recorded[-1]["status"] == "error"
