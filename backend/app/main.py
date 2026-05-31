from __future__ import annotations

import contextlib
import pathlib

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .application.agent_run_recorder import AgentRunRecorder
from .application.job_handlers import CommentJobHandler, CompactionJobHandler
from .application.pending_progress import PendingProgressProcessor
from .config import Settings, load_settings
from .db import init_db
from .errors import AppError, app_error_handler, generic_error_handler
from .infrastructure.audit import DefaultAuditSink
from .infrastructure.events import SSEEventPublisher
from .infrastructure.settings import SettingsProvider
from .middleware import RequestContextMiddleware
from .observability import setup_logging
from .repos.chunks import backfill_missing_chunks
from .services.job_runner import JobRunner
from .services.token_estimator import TokenEstimator

_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def _register_api_routers(app: FastAPI) -> None:
    from .routers.books import router as books_router
    from .routers.chapters import router as chapters_router
    from .routers.chat import router as chat_router
    from .routers.events import router as events_router
    from .routers.health import router as health_router
    from .routers.progress import router as progress_router
    from .routers.verify import router as verify_router
    from .routers.windows import router as windows_router

    for router in (
        health_router,
        books_router,
        chapters_router,
        chat_router,
        progress_router,
        events_router,
        verify_router,
        windows_router,
    ):
        app.include_router(router, prefix="/api")


def create_app() -> FastAPI:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.books_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(settings)

    app = FastAPI(title="vibe-reader-mini", version="0.1.0")
    app.state.settings = settings
    settings_provider = SettingsProvider(settings)
    app.state.settings_provider = settings_provider

    event_publisher = SSEEventPublisher()
    app.state.event_publisher = event_publisher

    estimator = TokenEstimator(settings.token_estimation)

    audit_sink = DefaultAuditSink()
    recorder = AgentRunRecorder(
        token_estimator=estimator,
        audit_sink=audit_sink,
    )
    pending_processor = PendingProgressProcessor(token_estimator=estimator)

    job_runner = JobRunner(
        settings_provider=settings_provider,
        max_concurrent=2,
        token_estimator=estimator,
        event_publisher=event_publisher,
        recorder=recorder,
        pending_processor=pending_processor,
    )
    job_runner.register_handler("comment_window", CommentJobHandler(job_runner))
    job_runner.register_handler("compact_context", CompactionJobHandler())
    app.state.job_runner = job_runner

    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, generic_error_handler)  # type: ignore[arg-type]

    _register_api_routers(app)

    frontend_dist = pathlib.Path(__file__).parent.parent.parent / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount(
            "/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend"
        )

    @app.on_event("startup")
    async def startup() -> None:
        db = await init_db(settings.db_path)
        app.state.db = db
        l2 = settings.context_l2

        # Load calibrations first so backfill uses calibrated metadata
        await estimator.load_calibrations(db)
        app.state.token_estimator = estimator

        est_info = estimator.get_calibration_info(settings.llm.model)
        backfilled = await backfill_missing_chunks(
            db,
            target_tokens=l2.target_chunk_tokens,
            min_tokens=l2.min_chunk_tokens,
            max_tokens=l2.max_chunk_tokens,
            max_chunk_chars=l2.max_chunk_chars,
            max_chunk_paragraphs=l2.max_chunk_paragraphs,
            estimator_model=est_info.get("model", "local_v1"),
            estimator_version=est_info.get("version", "local_v1"),
            estimator_calibration_ratio=est_info.get("calibration_ratio", 1.0),
            chunking_version="v1",
        )
        if backfilled:
            import logging

            logging.getLogger(__name__).info(
                "startup.chunk_backfill",
                extra={
                    "event": "startup.chunk_backfill",
                    "fields": {"chapters": backfilled},
                },
            )
        await estimator.load_calibrations(db)
        app.state.token_estimator = estimator

        await job_runner.start()
        await job_runner.recover_jobs(db)

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await job_runner.stop()
        db: object | None = getattr(app.state, "db", None)
        if db is not None:
            with contextlib.suppress(Exception):
                await db.close()  # type: ignore[union-attr]

    return app


def cli() -> None:
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    cli()
