from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from common import error_detail
from common.performance import JobContext, JobManager, JobNotFound, JobQueueFull

from ..config import SEARCH_MAX_RESULTS, SEARCH_MIN_LENGTH
from ..security import validate_public_id
from ..services.media import apply_organization_plan, build_organization_plan, is_qbittorrent_tree
from ..storage import copy_item, get_folder_sizes, search_files
from .csrf_guard import require_action_guard

router = APIRouter()


def _manager(request: Request) -> JobManager:
    return request.app.state.job_manager


def _validate_path(value: object, name: str = "path") -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise HTTPException(
            status_code=422,
            detail=error_detail("invalid_job_params", f"Paramètre {name} invalide.", "Corriger la demande"),
        )
    return value


def _bounded_int(value: object, *, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(name)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(name) from None
    return max(minimum, min(maximum, parsed))


def _build_factory(kind: str, params: Mapping) -> Callable[[JobContext], object]:
    if kind == "recursive_search":
        raw_query = params.get("q")
        query = raw_query.strip() if isinstance(raw_query, str) else ""
        if len(query) < SEARCH_MIN_LENGTH or len(query) > 200:
            raise HTTPException(
                status_code=422,
                detail=error_detail("invalid_search_query", "Saisir entre 3 et 200 caractères.", "Corriger la recherche"),
            )
        path = _validate_path(params.get("path", ""))
        try:
            offset = _bounded_int(params.get("offset", 0), name="offset", minimum=0, maximum=1_000_000)
            limit = _bounded_int(params.get("limit", 50), name="limit", minimum=1, maximum=200)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=error_detail("invalid_job_params", "Pagination invalide.", "Corriger la demande"),
            )

        def factory(context: JobContext) -> dict:
            return search_files(
                query,
                path,
                offset,
                limit,
                SEARCH_MAX_RESULTS,
                progress=lambda scanned, matches: context.report(
                    message=f"{scanned} entrées explorées, {matches} résultats",
                    progress=scanned,
                    total=None,
                ),
            )

        return factory

    if kind == "folder_sizes":
        raw_paths = params.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths or len(raw_paths) > 200:
            raise HTTPException(
                status_code=422,
                detail=error_detail("invalid_job_params", "Liste de dossiers invalide.", "Sélectionner entre 1 et 200 dossiers"),
            )
        paths = [_validate_path(value, "paths") for value in raw_paths]

        def factory(context: JobContext) -> dict:
            def on_entries(index: int, total: int, seen: int) -> None:
                context.report(
                    message=f"{index}/{total} dossiers · {seen} fichiers",
                    progress=max(0, index - 1),
                    total=total,
                )

            return get_folder_sizes(
                paths,
                progress=lambda current, total: context.report(
                    message=f"{current}/{total} dossiers",
                    progress=current,
                    total=total,
                ),
                entries_progress=on_entries,
            )

        return factory

    if kind in {"organize_preview", "organize_apply"}:
        path = _validate_path(params.get("path", ""))
        if is_qbittorrent_tree(path):
            raise HTTPException(
                status_code=409,
                detail=error_detail("qbit_organize_coordinated", "Ce dossier est géré par Torrent Panel.", "Utiliser le rangement du Torrent Panel"),
            )
        operation = build_organization_plan if kind == "organize_preview" else apply_organization_plan

        def factory(context: JobContext) -> dict:
            def progress_event(event: dict) -> None:
                raw_total = event.get("total")
                total = int(raw_total) if isinstance(raw_total, int) and raw_total > 0 else None
                raw_completed = event.get("completed")
                completed = int(raw_completed) if isinstance(raw_completed, int) else 0
                name = str(event.get("name") or "")[:200]
                category = str(event.get("category") or "")[:40]
                status = str(event.get("status") or "")[:20]
                context.report(
                    message=str(event.get("message") or "Rangement en cours")[:500],
                    progress=max(0, completed),
                    total=total,
                    data={
                        "phase": str(event.get("phase") or "")[:20],
                        "category": category,
                        "name": name,
                        "status": status,
                        "processed": int(event.get("processed") or 0),
                        "failed": int(event.get("failed") or 0),
                    },
                )

            context.report(
                message="Analyse du dossier" if kind == "organize_preview" else "Préparation du rangement",
                progress=0,
                total=None,
            )
            return operation(path, on_progress=progress_event)

        return factory

    if kind == "copy":
        raw_items = params.get("items")
        dest = _validate_path(params.get("dest", ""), "dest")
        if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 200:
            raise HTTPException(
                status_code=422,
                detail=error_detail("invalid_job_params", "Sélection de copie invalide.", "Sélectionner entre 1 et 200 éléments"),
            )
        items: list[dict[str, str]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise HTTPException(
                    status_code=422,
                    detail=error_detail("invalid_job_params", "Élément de copie invalide.", "Actualiser la sélection"),
                )
            items.append({
                "path": _validate_path(raw.get("path"), "items.path"),
                "name": _validate_path(raw.get("name"), "items.name"),
            })

        def factory(context: JobContext) -> dict:
            copied: list[dict] = []
            errors: list[dict] = []
            total = len(items)
            for index, item in enumerate(items, start=1):
                source_dir = item["path"].rsplit("/", 1)[0] if "/" in item["path"] else ""
                try:
                    copied.append(copy_item(source_dir, item["name"], dest))
                except Exception:
                    errors.append({
                        "name": item["name"],
                        "error": error_detail("copy_failed", "Copie impossible.", "Vérifier l’élément puis réessayer"),
                    })
                context.report(message=f"{index}/{total} éléments", progress=index, total=total)
            return {"success": not errors, "copied": len(copied), "items": copied, "errors": errors}

        return factory

    raise HTTPException(
        status_code=422,
        detail=error_detail("unknown_job_type", "Type de tâche inconnu.", "Choisir une action proposée"),
    )


@router.post("/jobs", status_code=202)
async def create_job(
    request: Request,
    _=Depends(require_action_guard),
    payload: dict = Body(...),
):
    kind = payload.get("type") if isinstance(payload, dict) else None
    params = payload.get("params") if isinstance(payload, dict) else None
    if not isinstance(kind, str) or not isinstance(params, dict):
        raise HTTPException(
            status_code=422,
            detail=error_detail("invalid_job", "Demande de tâche invalide.", "Corriger la demande"),
        )
    factory = _build_factory(kind, params)
    signature = hashlib.sha256(
        json.dumps(
            {"kind": kind, "params": params},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    try:
        job = await _manager(request).submit_thread(
            f"cloud:{kind}:{signature}",
            factory,
            metadata={"kind": kind},
        )
    except JobQueueFull:
        raise HTTPException(
            status_code=429,
            detail=error_detail("job_queue_full", "Trop de traitements en attente.", "Attendre la fin d’une tâche puis réessayer"),
        )
    return JSONResponse({"job": job}, status_code=202)


@router.get("/jobs")
async def list_jobs(request: Request):
    return {"jobs": await _manager(request).snapshot()}


@router.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str):
    try:
        job_id = validate_public_id(job_id, "identifiant de tâche")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=error_detail("invalid_job_id", "Identifiant de tâche invalide.", "Recharger la page"),
        )
    try:
        return await _manager(request).get(job_id)
    except JobNotFound:
        raise HTTPException(
            status_code=404,
            detail=error_detail("job_not_found", "Tâche introuvable.", "Relancer l’action si nécessaire"),
        )
