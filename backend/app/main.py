from __future__ import annotations

import contextlib
import pathlib

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .db import init_db
from .errors import AppError, app_error_handler, generic_error_handler
from .middleware import RequestContextMiddleware
from .observability import setup_logging

_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def create_app() -> FastAPI:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.books_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(settings)

    app = FastAPI(title="vibe-reader-mini", version="0.1.0")
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, generic_error_handler)  # type: ignore[arg-type]

    from .routers.health import router as health_router
    from .routers.books import router as books_router
    from .routers.chapters import router as chapters_router
    from .routers.progress import router as progress_router
    from .routers.events import router as events_router
    from .routers.verify import router as verify_router

    app.include_router(health_router, prefix="/api")
    app.include_router(books_router, prefix="/api")
    app.include_router(chapters_router, prefix="/api")
    app.include_router(progress_router, prefix="/api")
    app.include_router(events_router, prefix="/api")
    app.include_router(verify_router, prefix="/api")

    frontend_dist = pathlib.Path(__file__).parent.parent.parent / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    @app.on_event("startup")
    async def startup() -> None:
        db = await init_db(settings.db_path)
        app.state.db = db

    @app.on_event("shutdown")
    async def shutdown() -> None:
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
