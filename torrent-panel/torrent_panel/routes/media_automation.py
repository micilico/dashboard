"""Media automation workflow routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..models import RetryMediaWorkflow
from ..services.media_automation import MediaAutomationError
from .csrf_guard import require_action_guard
from common import error_detail

router = APIRouter()


@router.get("/media-workflows")
async def media_workflows(request: Request) -> dict[str, Any]:
    return request.app.state.media_automation.snapshot()


@router.post("/media-workflows/{entry_id}/retry", dependencies=[Depends(require_action_guard)])
async def retry_media_workflow(request: Request, entry_id: str, payload: RetryMediaWorkflow) -> dict[str, Any]:
    return {"entry": await request.app.state.media_automation.retry(entry_id, payload.scope)}


@router.post("/media-actions/{action}", dependencies=[Depends(require_action_guard)])
async def trigger_manual_media_action(request: Request, action: str) -> dict[str, str]:
    try:
        return await request.app.state.media_automation.manual_action(action)
    except MediaAutomationError as exc:
        raise HTTPException(
            status_code=502,
            detail=error_detail("media_action_failed", exc.public_message, "Réessayer"),
        ) from exc
