from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.get("/runtime")
async def runtime(request: Request) -> dict:
    from ..config import Settings

    settings: Settings = request.app.state.settings
    return {
        "app": "vibe-reader-mini",
        "version": "0.1.0",
        "data_dir": str(settings.data_dir),
        "verify_mode": settings.verify_mode,
        "llm": {
            "base_url_configured": bool(settings.llm.base_url),
            "api_key_configured": bool(settings.llm.api_key),
            "model": settings.llm.model,
        },
        "observability": {
            "enabled": settings.observability.enabled,
            "provider": settings.observability.provider,
        },
    }


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    from ..config import Settings

    settings: Settings = request.app.state.settings
    return {
        "reader": {
            "font_size": 18,
            "line_height": 1.7,
            "theme": "light",
        },
        "llm": {
            "base_url": settings.llm.base_url,
            "api_key_configured": bool(settings.llm.api_key),
            "model": settings.llm.model,
        },
        "context": {
            "effective_input_budget": settings.context.effective_input_budget,
            "hard_input_cap": settings.context.hard_input_cap,
        },
        "window": {
            "lookahead_paragraphs": settings.reader.lookahead_paragraphs,
            "target_window_tokens": settings.window.target_window_tokens,
            "max_window_tokens": settings.window.max_window_tokens,
        },
    }
