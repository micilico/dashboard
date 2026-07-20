"""Dashboard, activity, storage, media, health routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from ..services.monitoring import (
    activity_snapshot,
    dashboard_snapshot,
    health_snapshot,
    jellyfin_snapshot,
    storage_snapshot,
)
from .csrf_guard import set_csrf_cookie

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
