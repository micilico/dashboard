"""Dashboard, activity, storage, media, health, stats routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from common import error_detail

from ..models import RatioThresholdUpdate
from ..services.monitoring import (
    activity_snapshot,
    dashboard_snapshot,
    health_snapshot,
    jellyfin_snapshot,
    stats_snapshot,
    storage_snapshot,
)
from .csrf_guard import require_action_guard, set_csrf_cookie

router = APIRouter()


@router.get("/session")
async def session(request: Request, response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"csrfToken": set_csrf_cookie(request, response)}


@router.get("/dashboard")
async def dashboard(request: Request) -> dict[str, Any]:
    return await dashboard_snapshot(request.app)


@router.get("/activity")
async def activity(request: Request) -> dict[str, Any]:
    return await activity_snapshot(request.app)


@router.get("/storage")
async def storage(request: Request) -> dict[str, Any]:
    return await storage_snapshot(request.app)


@router.get("/media")
async def media(request: Request) -> dict[str, Any]:
    return await jellyfin_snapshot()


@router.get("/health/overview")
async def health_overview(request: Request) -> dict[str, Any]:
    return await health_snapshot(request.app)


@router.get("/stats")
async def stats(request: Request) -> dict[str, Any]:
    return await stats_snapshot(request.app)


@router.post("/stats/ratio-threshold", dependencies=[Depends(require_action_guard)])
async def ratio_threshold(request: Request, payload: RatioThresholdUpdate) -> dict[str, Any]:
    try:
        return {"settings": request.app.state.ratio_monitor.set_threshold(payload.threshold)}
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=error_detail("invalid_ratio_threshold", str(exc), "Choisir une valeur entre 1 et 100."),
        ) from exc
