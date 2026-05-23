from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..errors import AppError


class VerifyResetRequest(BaseModel):
    confirm_data_dir: str = Field(..., min_length=1)


router = APIRouter(tags=["verify"])


def _require_verify(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.verify_mode:
        raise AppError(
            "verify_mode_required",
            "Verify endpoints require VIBE_READER_VERIFY_MODE=1",
            status=404,
        )


@router.get("/verify/runtime")
async def verify_runtime(request: Request) -> dict[str, Any]:
    _require_verify(request)
    settings = request.app.state.settings
    return {
        "verify_mode": True,
        "data_dir": str(settings.data_dir),
        "app_version": "0.1.0",
        "git_commit": None,
        "git_dirty": True,
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


@router.get("/verify/jobs")
async def verify_list_jobs(
    request: Request,
    run_id: str | None = None,
    book_id: int | None = None,
    chapter_idx: int | None = None,
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List jobs for system verification.

    ``run_id`` scopes results to jobs linked to that verify run via
    ``verify_agent_runs`` (jobs without telemetry for the run are omitted).
    """
    _require_verify(request)
    from ..repos import jobs as job_repo

    db = request.app.state.db
    items, total = await job_repo.list_jobs(
        db,
        run_id=run_id,
        book_id=book_id,
        chapter_idx=chapter_idx,
        status=status,
        job_type=job_type,
        limit=limit,
    )
    return {"items": items, "total": total}


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
    settings = request.app.state.settings
    items = await list_agent_run_records(
        db,
        run_id=run_id,
        scenario_id=scenario_id,
        include_interaction=include_interaction,
        data_dir=settings.data_dir,
    )
    return {"items": items, "total": len(items)}


@router.get("/verify/metrics")
async def verify_metrics(
    request: Request,
    run_id: str = "",
    scenario_id: str | None = None,
) -> dict[str, Any]:
    _require_verify(request)
    from ..services.verify_telemetry import aggregate_metrics

    db = request.app.state.db
    return await aggregate_metrics(db, run_id=run_id, scenario_id=scenario_id)


@router.get("/verify/traces/{trace_id}/summary")
async def verify_trace_summary(
    request: Request,
    trace_id: str,
) -> dict[str, Any]:
    _require_verify(request)
    from ..services.verify_telemetry import get_trace_summary

    db = request.app.state.db
    summary = await get_trace_summary(db, trace_id)
    if summary is None:
        raise AppError(
            "trace_not_found",
            f"Trace summary not found for trace_id={trace_id}",
            status=404,
        )
    return summary


@router.post("/verify/llm-ping")
async def verify_llm_ping(request: Request) -> dict[str, Any]:
    """Minimal LLM connectivity probe for system verification (S0)."""
    _require_verify(request)
    settings = request.app.state.settings
    from ..services.llm_ping import ping_llm

    return await ping_llm(settings.llm, timeout_s=60.0)


@router.post("/verify/reset")
async def verify_reset(request: Request, body: VerifyResetRequest) -> dict[str, Any]:
    """Reset verification data directory (verify mode only)."""
    from ..services.verify_reset import reset_verify_data

    return await reset_verify_data(request, body.confirm_data_dir)
