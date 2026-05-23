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
            "provider_context_limit_tokens": settings.context.provider_context_limit_tokens,
            "attention_target_input_tokens": settings.context.attention_target_input_tokens,
            "emergency_input_cap_tokens": settings.context.emergency_input_cap_tokens,
        },
        "window_l1": {
            "lookahead_paragraphs": settings.reader.lookahead_paragraphs,
            "focus_target_tokens": settings.window_l1.focus_target_tokens,
            "focus_max_tokens": settings.window_l1.focus_max_tokens,
        },
    }
