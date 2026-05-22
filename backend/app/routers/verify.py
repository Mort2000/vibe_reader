from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..errors import AppError

router = APIRouter(tags=["verify"])


def _require_verify(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.verify_mode:
        raise AppError("verify_mode_required", "Verify endpoints require VIBE_READER_VERIFY_MODE=1", status=404)


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
    _require_verify(request)
    from ..repos import jobs as job_repo
    db = request.app.state.db
    items, total = await job_repo.list_jobs(
        db,
        book_id=book_id,
        chapter_idx=chapter_idx,
        status=status,
        job_type=job_type,
        limit=limit,
    )
    return {"items": items, "total": total}


@router.get("/verify/metrics")
async def verify_metrics(request: Request, run_id: str = "") -> dict[str, Any]:
    _require_verify(request)
    return {
        "run_id": run_id,
        "latency": {},
        "tokens": {},
        "cache": {
            "llm_prompt_cache_hit_rate": None,
            "llm_prompt_cache_hit_rate_available": False,
            "context_cache_hit_rate": None,
            "window_dedup_hit_rate": None,
            "comment_reuse_hit_rate": None,
        },
    }
