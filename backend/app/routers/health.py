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
    from .config import current_settings, runtime_summary

    return runtime_summary(current_settings(request))


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    from .config import current_settings, settings_summary

    return settings_summary(current_settings(request))
