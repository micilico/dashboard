"""Automation rule routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from .csrf_guard import require_action_guard
from ..models import AutomationRulePayload

router = APIRouter()


@router.get("/automations")
async def automations(request: Request) -> dict[str, Any]:
    return {"rules": request.app.state.automation_rules.snapshot()}


@router.post("/automations", dependencies=[Depends(require_action_guard)])
async def upsert_automation(request: Request, payload: AutomationRulePayload) -> dict[str, Any]:
    return {"rule": request.app.state.automation_rules.upsert(payload)}
