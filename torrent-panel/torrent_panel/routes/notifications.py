"""Notification routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from .csrf_guard import require_action_guard
from ..models import NotificationAction

router = APIRouter()


@router.get("/notifications")
async def notifications(request: Request) -> dict[str, Any]:
    dashboard = await _dashboard_snapshot_for_notifications(request.app)
    return {"notifications": request.app.state.notifications.reconcile(list(dashboard.get("alerts", [])))}


@router.post("/notifications/ack", dependencies=[Depends(require_action_guard)])
async def acknowledge_notification(request: Request, payload: NotificationAction) -> dict[str, Any]:
    return {"notification": request.app.state.notifications.acknowledge(payload.code)}


@router.post("/notifications/reopen", dependencies=[Depends(require_action_guard)])
async def reopen_notification(request: Request, payload: NotificationAction) -> dict[str, Any]:
    return {"notification": request.app.state.notifications.reopen(payload.code)}


def _dashboard_snapshot_for_notifications(app):
    from ..services.monitoring import dashboard_snapshot
    return dashboard_snapshot(app)
