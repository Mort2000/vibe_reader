from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from ..config_summary import runtime_summary, settings_summary
from ..infrastructure.settings import current_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.get("/runtime")
async def runtime(request: Request) -> dict:
    return runtime_summary(current_settings(request))


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    return settings_summary(current_settings(request))
