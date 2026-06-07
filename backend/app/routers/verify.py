from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..errors import AppError


router = APIRouter(tags=["verify"])


def _require_verify(request: Request) -> None:
    provider = getattr(request.app.state, "settings_provider", None)
    settings = provider.current() if provider is not None else request.app.state.settings
    if not settings.verify_mode:
        raise AppError(
            "verify_mode_required",
            "Verify endpoints require VIBE_READER_VERIFY_MODE=1",
            status=404,
        )


@router.get("/verify/agent-runs")
async def verify_agent_runs(
    request: Request,
    run_id: str = "",
    scenario_id: str | None = None,
    include_interaction: bool = True,
) -> dict[str, Any]:
    _require_verify(request)
    from ..services.verify_telemetry import list_agent_run_records

    db = request.app.state.db
    provider = getattr(request.app.state, "settings_provider", None)
    settings = provider.current() if provider is not None else request.app.state.settings
    items = await list_agent_run_records(
        db,
        run_id=run_id,
        scenario_id=scenario_id,
        include_interaction=include_interaction,
        data_dir=settings.data_dir,
    )
    return {"items": items, "total": len(items)}


@router.post("/verify/llm-ping")
async def verify_llm_ping(request: Request) -> dict[str, Any]:
    """Minimal LLM connectivity probe for system verification (S0)."""
    _require_verify(request)
    provider = getattr(request.app.state, "settings_provider", None)
    settings = provider.current() if provider is not None else request.app.state.settings
    from ..services.llm_ping import ping_llm

    return await ping_llm(settings.effective_llm("global"), timeout_s=60.0)
