from __future__ import annotations

import json
import hashlib
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from common import error_detail
from common.performance import JobNotFound, JobQueueFull

from ..config import HASH_RE, MEDIA_MOUNT_PATH, ORGANIZER_RECHECK_ENABLED, QBIT_SAVE_PATH
from ..services.organizer import apply_organization_plan, build_organization_plan
from ..services.relink import relink_missing
from .csrf_guard import require_action_guard
from .torrents import organize_library, organize_library_preview, organize_library_refresh
from ..models import LibraryOrganizeRequest

router = APIRouter()

JobType = Literal[
    "relink-preview",
    "relink",
    "organize-preview",
    "organize",
    "library-refresh",
    "library-preview",
    "library-apply",
]


class JobRequest(BaseModel):
    type: JobType
    params: dict[str, Any] = Field(default_factory=dict)


def _hashes(params: dict[str, Any], *, required: bool = False) -> list[str] | None:
    raw = params.get("hashes")
    if raw is None:
        if required:
            raise ValueError("Aucun torrent sélectionné.")
        return None
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise ValueError("Sélection de torrents invalide.")
    cleaned = [value.strip().lower() for value in raw]
    if any(not HASH_RE.fullmatch(value) for value in cleaned):
        raise ValueError("Hash torrent invalide.")
    if required and not cleaned:
        raise ValueError("Aucun torrent sélectionné.")
    return list(dict.fromkeys(cleaned)) or None


def _runner(request: Request, payload: JobRequest):
    hashes = _hashes(
        payload.params,
        required=payload.type == "organize",
    )

    if payload.type == "relink":
        recheck = payload.params.get("recheck", False)
        if not isinstance(recheck, bool):
            raise ValueError("Le paramètre recheck doit être un booléen.")
        mode = str(payload.params.get("mode") or "in_place")
        if mode not in {"in_place", "category"}:
            raise ValueError("Le mode de réparation doit être in_place ou category.")
    else:
        mode = "in_place"
        if "recheck" in payload.params:
            raise ValueError("Le paramètre recheck n'est pas accepté pour cette tâche.")

    async def run(context):
        if payload.type == "relink-preview":
            context.report(message="Analyse des fichiers à réparer.", progress=0, total=1)
            result = await relink_missing(request.app.state.qbit, hashes=hashes, preview=True)
        elif payload.type == "relink":
            context.report(message="Réparation des torrents.", progress=0, total=1)
            result = await relink_missing(
                request.app.state.qbit,
                hashes=hashes,
                preview=False,
                recheck=bool(recheck),
                mode=mode,
            )
        elif payload.type == "organize-preview":
            context.report(message="Analyse du rangement.", progress=0, total=1)
            result = {
                "plan": await build_organization_plan(
                    request.app.state.qbit,
                    QBIT_SAVE_PATH,
                    hashes=hashes,
                    mount_root=MEDIA_MOUNT_PATH,
                )
            }
        elif payload.type == "organize":
            context.report(message="Préparation du rangement.", progress=0, total=1)
            async with request.app.state.organize_lock:
                plan = await build_organization_plan(
                    request.app.state.qbit,
                    QBIT_SAVE_PATH,
                    hashes=hashes,
                    mount_root=MEDIA_MOUNT_PATH,
                )
                total = len(plan.get("entries", []))
                context.report(
                    message="Applications des changements.",
                    progress=0,
                    total=total,
                )

                def progress(event: dict[str, Any]) -> None:
                    context.report(
                        message=str(event.get("message") or "Rangement en cours."),
                        progress=int(event.get("index") or 0),
                        total=int(event.get("total") or total),
                        data={"stage": event.get("stage"), "hash": event.get("hash")},
                    )

                applied = await apply_organization_plan(
                    request.app.state.qbit,
                    plan,
                    progress=progress,
                    recheck=ORGANIZER_RECHECK_ENABLED,
                )
                result = {"plan": plan, "result": applied}
        elif payload.type == "library-refresh":
            context.report(message="Actualisation du montage média.")
            result = await organize_library_refresh(request)
        elif payload.type == "library-preview":
            context.report(message="Analyse des médias.")
            selected_hash = hashes[0] if hashes and len(hashes) == 1 else ""
            result = await organize_library_preview(request, selected_hash)
        else:
            context.report(message="Rangement de la bibliothèque.")
            library_payload = LibraryOrganizeRequest.model_validate(payload.params)
            result = await organize_library(request, library_payload)
        context.report(message="Tâche terminée.")
        return result

    key_params = dict(payload.params)
    key_params["hashes"] = sorted(hashes or [])
    serialized = json.dumps(key_params, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    key = f"{payload.type}:{digest}"
    return key, run


@router.post("/jobs", dependencies=[Depends(require_action_guard)])
async def create_job(request: Request, payload: JobRequest, response: Response) -> dict[str, Any]:
    try:
        key, runner = _runner(request, payload)
        job = await request.app.state.job_manager.submit_async(
            key,
            runner,
            metadata={"type": payload.type},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=error_detail("job_invalid", str(exc)[:300], "Vérifier la sélection"),
        ) from exc
    except JobQueueFull as exc:
        raise HTTPException(
            status_code=429,
            detail=error_detail(
                "job_queue_full",
                "Trop de tâches sont déjà en attente.",
                "Patienter puis réessayer",
            ),
        ) from exc
    response.status_code = 202
    response.headers["Location"] = f"{request.url.path.rstrip('/')}/{job['id']}"
    return job


@router.get("/jobs")
async def list_jobs(request: Request) -> dict[str, Any]:
    return {"jobs": await request.app.state.job_manager.snapshot()}


@router.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> dict[str, Any]:
    try:
        return await request.app.state.job_manager.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("job_not_found", "Tâche introuvable.", "Recharger la page"),
        ) from exc
