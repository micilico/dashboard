"""Torrent CRUD routes."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .csrf_guard import require_action_guard
from ..config import (
    ALLOWED_SAVE_PATHS,
    HASH_RE,
    MEDIA_MOUNT_PATH,
    QBIT_SAVE_PATH,
    TRACKER_INDEX_CACHE_TTL_SECONDS,
    TR4KER_ANNOUNCE_URL,
)
from ..models import (
    AddMagnet,
    AddTrackerPayload,
    DeleteTorrent,
    ForceStartTorrent,
    LibraryOrganizeRequest,
    RelinkRequest,
    OrganizeRequest,
    TorrentCategoryUpdate,
    TorrentHashesAction,
    TorrentRateLimitUpdate,
    TorrentSequentialUpdate,
    TorrentTagsUpdate,
)
from ..qbittorrent import QbitError
from ..services.relink import relink_missing
from ..services.organizer import apply_organization_plan, build_organization_plan, duplicate_cloud_operations, organization_categories, organization_plan_revalidation_reason, orphan_operations, verify_duplicate_group
from ..services.cloud_panel import CloudPanelError, arrange_batch
from ..services.organization_runs import journal_duplicate_operation, journal_operation, journal_orphan_operation
from ..services.media_automation import MediaAutomationError
from common import error_detail
logger = logging.getLogger("torrent_panel.routes.trackers")

_TRACKER_SPECIAL_PREFIXES = ("** [DHT]", "** [PeX]", "** [LSD]", "** [Metadata]")

_TRACKER_INDEX_CACHE: dict[str, Any] = {"key": None, "data": None, "ts": 0.0, "task": None}
_TRACKER_CACHE_LOCK = asyncio.Lock()
_TRACKER_CONCURRENCY = 6

TRACKER_VALID_SCHEMES = ("http://", "https://", "udp://", "ws://", "wss://")
_URL_IN_TEXT_RE = re.compile(r"(?:https?|udp|wss?)://\S+", re.IGNORECASE)

router = APIRouter()


def invalidate_tracker_index() -> None:
    _TRACKER_INDEX_CACHE.update({"key": None, "data": None, "ts": 0.0})


def validate_hash(torrent_hash: str) -> str:
    if not HASH_RE.fullmatch(torrent_hash):
        raise HTTPException(
            status_code=422,
            detail=error_detail("hash_invalid", "Hash torrent invalide.", "Réessayer"),
        )
    return torrent_hash.lower()


def validate_hashes(torrent_hashes: list[str]) -> list[str]:
    cleaned = [validate_hash(item) for item in torrent_hashes]
    if not cleaned:
        raise HTTPException(status_code=422, detail=error_detail("hash_invalid", "Aucun torrent sélectionné.", "Réessayer"))
    return cleaned


def validate_magnet(magnet: str) -> tuple[str | None, str | None]:
    candidate = magnet.strip()
    if not candidate.startswith("magnet:?"):
        return None, "Lien magnet invalide."
    if "xt=urn:btih:" not in candidate and "xt=urn:btmh:" not in candidate:
        return None, "Lien magnet sans identifiant torrent."
    return candidate, None


def _magnet_with_tracker(magnet: str, tracker_url: str) -> str:
    parsed = urlparse(magnet)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    existing_trackers = {value.strip() for key, value in query if key == "tr"}
    if tracker_url in existing_trackers:
        return magnet
    query.append(("tr", tracker_url))
    encoded = urlencode(query, doseq=True, quote_via=quote)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, encoded, parsed.fragment))


def qbit_error_response(exc: QbitError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=error_detail(exc.code, exc.public_message, exc.recovery))


@router.get("/torrents")
async def torrents(request: Request) -> dict[str, object]:
    try:
        return {"torrents": await request.app.state.qbit.torrents()}
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@router.post("/torrents/pause", dependencies=[Depends(require_action_guard)])
async def pause_torrent(request: Request, payload: TorrentHashesAction) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.pause_many(hashes)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "paused", "count": len(hashes)}


@router.post("/torrents/resume", dependencies=[Depends(require_action_guard)])
async def resume_torrent(request: Request, payload: TorrentHashesAction) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.resume_many(hashes)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "resumed", "count": len(hashes)}


@router.post("/torrents/delete", dependencies=[Depends(require_action_guard)])
async def delete_torrent(request: Request, payload: DeleteTorrent) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.delete_many(hashes, payload.deleteFiles)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    invalidate_tracker_index()
    return {"status": "deleted", "count": len(hashes)}


@router.post("/torrents/force-start", dependencies=[Depends(require_action_guard)])
async def force_start_torrent(request: Request, payload: ForceStartTorrent) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.set_force_start_many(hashes, payload.enabled)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "force_start_updated", "enabled": payload.enabled, "count": len(hashes)}


@router.post("/torrents/recheck", dependencies=[Depends(require_action_guard)])
async def recheck_torrents(request: Request, payload: TorrentHashesAction) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.recheck_many(hashes)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "recheck_requested", "count": len(hashes)}


@router.post("/torrents/reannounce", dependencies=[Depends(require_action_guard)])
async def reannounce_torrents(request: Request, payload: TorrentHashesAction) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.reannounce_many(hashes)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "reannounce_requested", "count": len(hashes)}


@router.post("/torrents/set-category", dependencies=[Depends(require_action_guard)])
async def set_torrent_category(request: Request, payload: TorrentCategoryUpdate) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.set_category_many(hashes, payload.category.strip())
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "category_updated", "count": len(hashes), "category": payload.category.strip()}


@router.post("/torrents/add-tags", dependencies=[Depends(require_action_guard)])
async def add_torrent_tags(request: Request, payload: TorrentTagsUpdate) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.add_tags_many(hashes, payload.tags.strip())
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "tags_updated", "count": len(hashes), "tags": payload.tags.strip()}


@router.post("/torrents/set-download-limit", dependencies=[Depends(require_action_guard)])
async def set_torrent_download_limit(request: Request, payload: TorrentRateLimitUpdate) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.set_download_limit_many(hashes, payload.limitKiB * 1024)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "download_limit_updated", "count": len(hashes), "limitKiB": payload.limitKiB}


@router.post("/torrents/set-upload-limit", dependencies=[Depends(require_action_guard)])
async def set_torrent_upload_limit(request: Request, payload: TorrentRateLimitUpdate) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.set_upload_limit_many(hashes, payload.limitKiB * 1024)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "upload_limit_updated", "count": len(hashes), "limitKiB": payload.limitKiB}


@router.get("/torrents/relink-preview")
async def relink_preview(request: Request) -> dict[str, Any]:
    try:
        return await relink_missing(request.app.state.qbit, preview=True)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@router.get("/torrents/relink-status")
async def relink_status(request: Request) -> dict[str, Any]:
    """Cheap indicator (no filesystem scan) of torrents that likely need relinking."""
    from ..services.relink import relink_status as compute_relink_status

    try:
        return await compute_relink_status(request.app.state.qbit)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@router.post("/torrents/relink", dependencies=[Depends(require_action_guard)])
async def relink_torrents(request: Request, payload: RelinkRequest) -> dict[str, Any]:
    try:
        hashes = [validate_hash(item) for item in payload.hashes]
        return await relink_missing(
            request.app.state.qbit,
            hashes=hashes,
            preview=payload.preview,
            recheck=payload.recheck,
        )
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@router.get("/torrents/organize-preview")
async def organize_torrents_preview(
    request: Request,
    torrent_hash: str = Query(default="", alias="hash"),
) -> dict[str, Any]:
    """Preview a Jellyfin layout derived exclusively from qBittorrent-owned files."""
    hashes = [validate_hash(torrent_hash)] if torrent_hash else None
    try:
        return {
            "plan": await build_organization_plan(
                request.app.state.qbit,
                QBIT_SAVE_PATH,
                hashes=hashes,
                mount_root=MEDIA_MOUNT_PATH,
            )
        }
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@router.get("/torrents/organize-status")
async def organize_torrents_status(request: Request) -> dict[str, Any]:
    return {"pending": 0, "hashes": [], "automaticResume": False}


@router.post("/torrents/organize", dependencies=[Depends(require_action_guard)])
async def organize_torrents(request: Request, payload: OrganizeRequest) -> dict[str, Any]:
    """Pause, organize through qBittorrent, verify, then refresh rclone/Jellyfin."""
    hashes = [validate_hash(value) for value in payload.hashes]
    try:
        async with request.app.state.organize_lock:
            plan = await build_organization_plan(
                request.app.state.qbit,
                QBIT_SAVE_PATH,
                hashes=hashes,
                mount_root=MEDIA_MOUNT_PATH,
            )
            result = await apply_organization_plan(request.app.state.qbit, plan)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc

    refresh = {"rclone": "skipped", "jellyfin": "skipped"}
    if result["organized"]:
        changed_categories = organization_categories(plan)
        refresh_categories = set(changed_categories)
        video_categories = sorted(refresh_categories & {"Films", "Series"})
        if video_categories:
            # Preserve the existing full video-root refresh while unrelated
            # game/book plans only invalidate their own directories.
            refresh_categories.update({"Films", "Series"})
        try:
            await request.app.state.media_automation.refresh_rclone(
                dirs=[f"qbittorrent/{category}" for category in sorted(refresh_categories)] or ["qbittorrent"],
                recursive=True,
                async_run=True,
            )
            refresh["rclone"] = "requested"
        except MediaAutomationError as exc:
            refresh["rclone"] = "failed"
            refresh["rcloneMessage"] = exc.public_message
        if video_categories:
            try:
                await request.app.state.media_automation.trigger_jellyfin_scan([])
                refresh["jellyfin"] = "requested"
            except MediaAutomationError as exc:
                refresh["jellyfin"] = "failed"
                refresh["jellyfinMessage"] = exc.public_message
    return {"plan": plan, "result": result, "refresh": refresh}


async def _library_plan(request: Request, hashes: list[str] | None) -> dict[str, Any]:
    inventory = (
        await request.app.state.library_inventory.get(MEDIA_MOUNT_PATH, QBIT_SAVE_PATH)
        if MEDIA_MOUNT_PATH else None
    )
    plan = await build_organization_plan(
        request.app.state.qbit,
        QBIT_SAVE_PATH,
        hashes=hashes,
        mount_root=MEDIA_MOUNT_PATH,
        metadata_resolver=request.app.state.metadata_resolver,
        inventory=inventory,
    )
    if inventory is not None:
        plan["inventoryGeneration"] = inventory.generation
    return plan


@router.get("/torrents/organize-library-preview")
async def organize_library_preview(
    request: Request,
    torrent_hash: str = Query(default="", alias="hash"),
) -> dict[str, Any]:
    """Create a non-destructive global library organization plan."""
    hashes = [validate_hash(torrent_hash)] if torrent_hash else []
    logger.info("organize-library preview started: selected_torrents=%d", len(hashes))
    try:
        plan = await _library_plan(request, hashes or None)
    except QbitError as exc:
        logger.warning("organize-library preview failed: code=%s", exc.code)
        raise qbit_error_response(exc) from exc
    run = request.app.state.organization_runs.create(hashes)
    request.app.state.organization_runs.update(
        run["runId"],
        plan=plan,
        sourceFingerprint=plan.get("sourceFingerprint", ""),
        inventoryGeneration=plan.get("inventoryGeneration", ""),
        planCreatedAt=time.time(),
        warnings=plan.get("warnings", []),
    )
    logger.info(
        "organize-library preview completed: torrents=%d duplicates=%d warnings=%d",
        len(plan.get("entries", [])),
        len(plan.get("duplicateGroups", [])),
        len(plan.get("warnings", [])),
    )
    return {"runId": run["runId"], "plan": plan}


@router.post("/torrents/organize-library-refresh", dependencies=[Depends(require_action_guard)])
async def organize_library_refresh(request: Request) -> dict[str, str]:
    """Refresh the current qBittorrent library before building its plan."""
    try:
        await request.app.state.media_automation.refresh_rclone(
            dirs=["qbittorrent"],
            recursive=True,
            async_run=False,
            timeout_seconds=10,
        )
    except MediaAutomationError as exc:
        raise HTTPException(
            status_code=502,
            detail=error_detail("rclone_refresh_failed", exc.public_message, "Vérifier rclone puis réessayer"),
        ) from exc
    # Le refresh rclone peut révéler des fichiers apparus depuis le dernier
    # inventaire ; ne pas réutiliser le snapshot précédent pour la preview.
    request.app.state.library_inventory.invalidate()
    return {"status": "refreshed", "path": "qbittorrent"}


@router.post("/torrents/organize-library", dependencies=[Depends(require_action_guard)])
async def organize_library(request: Request, payload: LibraryOrganizeRequest) -> dict[str, Any]:
    """Apply a selected global plan; every torrent remains paused afterward."""
    hashes = [validate_hash(value) for value in payload.hashes]
    logger.info(
        "organize-library apply started: torrents=%d orphans=%d duplicates=%d",
        len(hashes),
        len(payload.orphanPaths),
        len(payload.duplicateGroupIds),
    )
    async with request.app.state.organize_lock:
        try:
            run = request.app.state.organization_runs.get(payload.runId) if payload.runId else None
            if run and run.get("status") == "completed":
                raise HTTPException(
                    status_code=409,
                    detail=error_detail("organization_already_applied", "Ce plan a déjà été appliqué.", "Relancer une analyse"),
                )
            if run and run.get("status") == "applying":
                raise HTTPException(
                    status_code=409,
                    detail=error_detail("organization_in_progress", "Ce plan est déjà en cours d’application.", "Patienter puis actualiser"),
                )
            if payload.runId and run is None:
                raise HTTPException(
                    status_code=404,
                    detail=error_detail("organization_run_not_found", "Exécution introuvable.", "Relancer une analyse"),
                )
            stored_plan = run.get("plan") if isinstance(run, dict) else None
            plan_is_fresh = (
                isinstance(stored_plan, dict)
                and isinstance(run.get("planCreatedAt"), (int, float))
                and time.time() - float(run["planCreatedAt"]) <= 900
            )
            if run and not plan_is_fresh:
                raise HTTPException(
                    status_code=409,
                    detail=error_detail("organization_plan_expired", "Ce plan a expiré ou n’est plus disponible.", "Relancer une analyse"),
                )
            if plan_is_fresh:
                selected_hashes_for_validation = set(hashes) or {
                    str(entry.get("hash") or "").lower()
                    for entry in stored_plan.get("entries", [])
                    if isinstance(entry, dict)
                }
                stale_reason = await organization_plan_revalidation_reason(
                    request.app.state.qbit,
                    stored_plan,
                    selected_hashes_for_validation,
                )
                if stale_reason:
                    raise HTTPException(status_code=409, detail=error_detail("organization_plan_stale", "Les torrents ont changé depuis l’analyse.", "Relancer une analyse"))
                inventory_generation = stored_plan.get("inventoryGeneration") or run.get("inventoryGeneration")
                if MEDIA_MOUNT_PATH and inventory_generation:
                    current_inventory = await request.app.state.library_inventory.get(MEDIA_MOUNT_PATH, QBIT_SAVE_PATH, force=True)
                    if current_inventory.generation != inventory_generation:
                        raise HTTPException(status_code=409, detail=error_detail("organization_plan_stale", "La bibliothèque a changé depuis l’analyse.", "Relancer une analyse"))
                plan = stored_plan
                selected_hashes = set(hashes)
                plan = dict(plan)
                plan["entries"] = [entry for entry in plan.get("entries", []) if not selected_hashes or str(entry.get("hash") or "").lower() in selected_hashes]
                plan["orphanMedia"] = [item for item in plan.get("orphanMedia", []) if not payload.orphanPaths or str(item.get("path")) in set(payload.orphanPaths)]
                plan["duplicateGroups"] = [group for group in plan.get("duplicateGroups", []) if not payload.duplicateGroupIds or str(group.get("id")) in set(payload.duplicateGroupIds)]
                plan["totalOperations"] = sum(len(entry.get("operations", [])) for entry in plan["entries"])
            else:
                await request.app.state.media_automation.refresh_rclone(dirs=["qbittorrent"], recursive=True, async_run=False, timeout_seconds=10)
                plan = await _library_plan(request, None if payload.orphanPaths else (hashes or None))
                if payload.orphanPaths:
                    selected_hashes = set(hashes)
                    plan["entries"] = [entry for entry in plan.get("entries", []) if str(entry.get("hash") or "").lower() in selected_hashes]
                    plan["totalOperations"] = sum(len(entry.get("operations", [])) for entry in plan["entries"])
            if run is None:
                run = request.app.state.organization_runs.create(hashes)
                request.app.state.organization_runs.update(
                    run["runId"],
                    plan=plan,
                    planCreatedAt=time.time(),
                    warnings=plan.get("warnings", []),
                )

            entry_total = len(plan.get("entries", []))
            progress_items: dict[str, dict[str, Any]] = {}
            last_progress = {"index": 0, "total": entry_total, "processed": 0}
            last_progress_write = {"at": 0.0}

            def publish_stage(
                stage: str,
                *,
                index: int | None = None,
                total: int | None = None,
                processed: int | None = None,
                hash_value: str = "",
                message: str = "",
                failed_stage: str = "",
                force: bool = False,
            ) -> None:
                if index is not None:
                    last_progress["index"] = index
                if total is not None:
                    last_progress["total"] = total
                if processed is not None:
                    last_progress["processed"] = processed
                if hash_value:
                    progress_items[hash_value] = {
                        "stage": stage,
                        "message": message[:300],
                        "updatedAt": time.time(),
                    }
                now = time.monotonic()
                if not force and stage not in {"jellyfin", "failed", "completed", "partial_failure"} and now - last_progress_write["at"] < 0.75:
                    return
                last_progress_write["at"] = now
                request.app.state.organization_runs.update(
                    run["runId"],
                    stage=stage,
                    progress={
                        **last_progress,
                        "stage": stage,
                        "message": message[:300],
                        "hash": hash_value,
                        "failedStage": failed_stage[:40],
                        "items": dict(progress_items),
                    },
                )

            def report_progress(event: dict[str, Any]) -> None:
                publish_stage(
                    str(event.get("stage") or "applying"),
                    index=event.get("index"),
                    total=event.get("total"),
                    processed=event.get("processed"),
                    hash_value=str(event.get("hash") or ""),
                    message=str(event.get("message") or ""),
                    failed_stage=str(event.get("failedStage") or ""),
                    force=str(event.get("stage") or "") == "failed",
                )

            run = request.app.state.organization_runs.update(
                run["runId"],
                status="applying",
                stage="preparing",
                progress={
                    **last_progress,
                    "stage": "preparing",
                    "message": "Sélection prête — démarrage du rangement.",
                    "hash": "",
                    "items": {},
                },
            ) or run
            result = await apply_organization_plan(request.app.state.qbit, plan, None, progress=report_progress)
        except QbitError as exc:
            if run:
                request.app.state.organization_runs.update(run["runId"], status="preview", stage="failed")
            raise qbit_error_response(exc) from exc
        except HTTPException:
            # Ne jamais rétrograder un run déjà en cours : un second appel
            # concurrent doit obtenir sa 409 sans casser le suivi du premier.
            raise
        except Exception:
            if run and run.get("status") == "applying":
                request.app.state.organization_runs.update(run["runId"], status="preview", stage="failed")
            raise

        if run is None:
            run = request.app.state.organization_runs.create(hashes)
        last_progress["processed"] = int(result.get("organized", 0))
        orphan_paths = set(payload.orphanPaths)
        duplicate_results: list[dict[str, Any]] = []
        selected_duplicate_ids = set(payload.duplicateGroupIds)
        if selected_duplicate_ids:
            publish_stage("duplicates", message="Fusion des doublons sélectionnés.")
        for group in plan.get("duplicateGroups", []):
            if str(group.get("id")) not in selected_duplicate_ids:
                continue
            if group.get("status") == "verification_required":
                verify_duplicate_group(group, MEDIA_MOUNT_PATH, QBIT_SAVE_PATH)
            if group.get("status") != "ready":
                duplicate_results.append({"id": group.get("id"), "success": False, "error": "Conflit ou validation manuelle requise"})
                continue
            torrent_hashes = [str(item.get("hash")) for item in group.get("associatedTorrents", []) if item.get("hash")]
            try:
                if torrent_hashes:
                    await request.app.state.qbit.pause_many(torrent_hashes)
                cloud_result = await arrange_batch(duplicate_cloud_operations(group, QBIT_SAVE_PATH))
                if cloud_result.get("failed", 0):
                    raise CloudPanelError("Une opération de fusion a échoué.")
                if torrent_hashes:
                    await request.app.state.qbit.recheck_many(torrent_hashes)
                duplicate_results.append({"id": group.get("id"), "success": True, "status": "verification", "torrentHashes": torrent_hashes})
            except (CloudPanelError, QbitError) as exc:
                duplicate_results.append({"id": group.get("id"), "success": False, "error": getattr(exc, "message", getattr(exc, "public_message", "Fusion impossible")), "torrentHashes": torrent_hashes})
        result["duplicateGroups"] = duplicate_results
        result["duplicatesMerged"] = sum(1 for item in duplicate_results if item.get("success"))
        if not payload.hashes and not payload.orphanPaths:
            orphan_paths = {str(item.get("path")) for item in plan.get("orphanMedia", [])}
        orphan_items = [item for item in plan.get("orphanMedia", []) if str(item.get("path")) in orphan_paths]
        if orphan_items:
            publish_stage("orphans", message="Rangement des médias orphelins sélectionnés.")
        prepared_orphan_operations = {
            str(item.get("path") or ""): orphan_operations(item, QBIT_SAVE_PATH)
            for item in orphan_items
        }
        orphan_spans: list[tuple[str, int, int]] = []
        orphan_batch_operations: list[dict[str, Any]] = []
        for item in orphan_items:
            path = str(item.get("path") or "")
            operations = prepared_orphan_operations.get(path)
            if operations is None:
                continue
            start = len(orphan_batch_operations)
            orphan_batch_operations.extend(operations)
            orphan_spans.append((path, start, len(operations)))
        orphan_cloud_results: dict[str, dict[str, Any]] = {}
        orphan_batch_error: str | None = None
        if orphan_batch_operations:
            try:
                orphan_batch = await arrange_batch(orphan_batch_operations)
                batch_items = orphan_batch.get("results")
                if not isinstance(batch_items, list) or len(batch_items) != len(orphan_batch_operations):
                    orphan_batch_error = "Réponse cloud-panel incomplète."
                else:
                    for path, start, count in orphan_spans:
                        unit_results = batch_items[start:start + count]
                        orphan_cloud_results[path] = {
                            "results": unit_results,
                            "failed": sum(1 for result in unit_results if not result.get("success")),
                        }
            except CloudPanelError as exc:
                orphan_batch_error = exc.message
        orphan_results: list[dict[str, Any]] = []
        orphan_journal: list[dict[str, Any]] = []
        for orphan in orphan_items:
            operations = prepared_orphan_operations.get(str(orphan.get("path") or ""))
            if operations is None:
                orphan_result = {"path": orphan.get("path", ""), "success": False, "error": "Identification ambiguë"}
                orphan_results.append(orphan_result)
                orphan_journal.append(journal_orphan_operation(orphan, [], orphan_result))
                continue
            try:
                cloud_result = orphan_cloud_results.get(str(orphan.get("path") or ""))
                if cloud_result is None:
                    raise CloudPanelError(orphan_batch_error or "cloud-panel n'a pas renvoyé de résultat.")
                orphan_result = {
                    "path": orphan.get("path", ""),
                    "success": cloud_result.get("failed", 0) == 0,
                    "message": "Média rangé — aucun torrent associé" if cloud_result.get("failed", 0) == 0 else "Opération partiellement échouée",
                }
            except CloudPanelError as exc:
                orphan_result = {"path": orphan.get("path", ""), "success": False, "error": exc.message}
            orphan_results.append(orphan_result)
            orphan_journal.append(journal_orphan_operation(orphan, operations, orphan_result))
        result["orphansOrganized"] = sum(1 for item in orphan_results if item.get("success"))
        result["orphanResults"] = orphan_results
        by_hash = {str(item.get("hash")): item for item in result.get("results", [])}
        journal = [
            journal_operation(entry, by_hash.get(str(entry.get("hash")), {"success": False, "error": "Non traité"}))
            for entry in plan.get("entries", [])
        ]
        journal.extend(orphan_journal)
        duplicate_by_id = {str(item.get("id")): item for item in duplicate_results}
        journal.extend(
            journal_duplicate_operation(group, duplicate_by_id.get(str(group.get("id")), {"success": False, "error": "Non sélectionné"}))
            for group in plan.get("duplicateGroups", [])
            if str(group.get("id")) in selected_duplicate_ids
        )
        has_failure = bool(result.get("failed")) or any(not item.get("success") for item in orphan_results) or any(not item.get("success") for item in duplicate_results)
        publish_stage("finalizing", message="Consolidation du journal de rangement.")
        stored = request.app.state.organization_runs.update(
            run["runId"],
            status="completed" if not has_failure else "partial_failure",
            plan=plan,
            result=result,
            operations=journal,
            warnings=plan.get("warnings", []),
        )
        if result.get("organized") or result.get("orphansOrganized") or result.get("duplicatesMerged"):
            request.app.state.library_inventory.invalidate()

        logger.info(
            "organize-library apply completed: organized=%d failed=%d duplicates_merged=%d orphans=%d",
            int(result.get("organized", 0)),
            int(result.get("failed", 0)),
            int(result.get("duplicatesMerged", 0)),
            int(result.get("orphansOrganized", 0)),
        )

    refresh = {"rclone": "skipped", "jellyfin": "skipped"}
    if result.get("organized") or result.get("orphansOrganized"):
        changed_categories = set(organization_categories(plan))
        refresh_categories = set(changed_categories)
        video_categories = sorted(refresh_categories & {"Films", "Series"})
        if video_categories:
            refresh_categories.update({"Films", "Series"})
        publish_stage("rclone", message="Actualisation du montage rclone.")
        try:
            await request.app.state.media_automation.refresh_rclone(
                dirs=[f"qbittorrent/{category}" for category in sorted(refresh_categories)] or ["qbittorrent"], recursive=True, async_run=True
            )
            refresh["rclone"] = "requested"
        except MediaAutomationError as exc:
            refresh["rclone"] = "failed"
            refresh["rcloneMessage"] = exc.public_message
        if video_categories:
            publish_stage("jellyfin", message="Scan de la bibliothèque Jellyfin.")
            try:
                library_ids = sorted({request.app.state.media_automation._library_for_category(category) for category in video_categories if request.app.state.media_automation._library_for_category(category)})
                await request.app.state.media_automation.trigger_jellyfin_scan(library_ids)
                refresh["jellyfin"] = "requested"
            except MediaAutomationError as exc:
                refresh["jellyfin"] = "failed"
                refresh["jellyfinMessage"] = exc.public_message
    publish_stage(
        "completed" if not has_failure else "partial_failure",
        message="Rangement terminé." if not has_failure else "Rangement terminé avec des éléments à vérifier.",
        force=True,
    )
    return {"runId": stored["runId"] if stored else run["runId"], "plan": plan, "result": result, "refresh": refresh}


@router.get("/torrents/organize-library-runs/{run_id}")
async def organize_library_run(request: Request, run_id: str) -> dict[str, Any]:
    run = request.app.state.organization_runs.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=error_detail("organization_run_not_found", "Exécution introuvable.", "Relancer une analyse"),
        )
    return run


@router.post("/torrents/set-sequential", dependencies=[Depends(require_action_guard)])
async def set_torrent_sequential(request: Request, payload: TorrentSequentialUpdate) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.set_sequential_download_many(hashes, payload.enabled)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "sequential_updated", "count": len(hashes), "enabled": payload.enabled}


@router.get("/torrents/stats/trackers")
async def torrent_tracker_stats(request: Request) -> dict[str, Any]:
    try:
        torrents = await request.app.state.qbit.torrents()
        tracker_payload = await _tracker_index_payload(request, torrents=torrents)
        stats = await asyncio.to_thread(
            request.app.state.tracker_stats.observe,
            torrents,
            tracker_payload["index"],
        )
        return {"stats": stats}
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@router.get("/torrents/{torrent_hash}/trackers")
async def torrent_trackers(request: Request, torrent_hash: str) -> dict[str, Any]:
    try:
        trackers = await request.app.state.qbit.trackers(validate_hash(torrent_hash))
        public_trackers = []
        for tracker in trackers:
            if not isinstance(tracker, dict):
                continue
            public_tracker = {
                key: value
                for key, value in tracker.items()
                if key in {"status", "tier", "num_peers", "num_seeds", "num_leeches", "num_downloaded", "msg"}
            }
            raw_url = str(tracker.get("url") or "")
            public_tracker["url"] = raw_url if _is_special_tracker(raw_url) else _public_announce_url(raw_url)
            if "msg" in public_tracker:
                public_tracker["msg"] = _URL_IN_TEXT_RE.sub("[URL masquée]", str(public_tracker["msg"]))
            public_trackers.append(public_tracker)
        return {"trackers": public_trackers}
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@router.get("/torrents/{torrent_hash}/files")
async def torrent_files(request: Request, torrent_hash: str) -> dict[str, Any]:
    try:
        return {"files": await request.app.state.qbit.files(validate_hash(torrent_hash))}
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


def _is_special_tracker(url: str) -> bool:
    return url.startswith(_TRACKER_SPECIAL_PREFIXES)


def _tracker_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port
        if port and port not in {80, 443}:
            return f"{host}:{port}"
        return host
    except Exception:
        return url


def _public_announce_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not parsed.scheme or not host:
            return _tracker_domain(url)
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        return f"{parsed.scheme}://{netloc}{parsed.path or ''}"
    except Exception:
        return _tracker_domain(url)


def _validate_tracker_url(url: str) -> str | None:
    stripped = url.strip()
    if not stripped:
        return "Adresse vide."
    if not stripped.startswith(TRACKER_VALID_SCHEMES):
        return "Protocole non pris en charge. Utilisez http://, https://, udp://, ws:// ou wss://."
    return None


def _sanitize_tracker_for_logs(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return f"{parsed.scheme}://{host}{':' + str(parsed.port) if parsed.port else ''}"
    except Exception:
        return "<tracker>"


@router.get("/trackers/index")
async def tracker_index(request: Request) -> dict[str, Any]:
    try:
        return await _tracker_index_payload(request)
    except QbitError as exc:
        cache = _TRACKER_INDEX_CACHE
        if cache["data"] is not None:
            return cache["data"]
        raise qbit_error_response(exc) from exc


async def _tracker_index_payload(request: Request, torrents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if torrents is None:
        torrents = await request.app.state.qbit.torrents()

    cache_key = tuple(sorted(str(item.get("hash") or "").lower() for item in torrents if item.get("hash")))
    now = time.monotonic()
    cache = _TRACKER_INDEX_CACHE
    async with _TRACKER_CACHE_LOCK:
        if cache["key"] == cache_key and cache["data"] is not None and now - cache["ts"] < TRACKER_INDEX_CACHE_TTL_SECONDS:
            return cache["data"]
        task = cache.get("task")
        if cache["key"] != cache_key or task is None:
            task = asyncio.create_task(_build_tracker_index(request, torrents))
            cache.update({"key": cache_key, "task": task})
    try:
        payload = await task
    except Exception:
        async with _TRACKER_CACHE_LOCK:
            if cache.get("task") is task:
                cache["task"] = None
        raise
    async with _TRACKER_CACHE_LOCK:
        if cache.get("task") is task:
            cache.update({"data": payload, "ts": time.monotonic(), "task": None})
    return payload


async def _build_tracker_index(request: Request, torrents: list[dict[str, Any]]) -> dict[str, Any]:

    sem = asyncio.Semaphore(_TRACKER_CONCURRENCY)

    async def fetch_trackers(torrent_hash: str) -> list[dict[str, Any]]:
        async with sem:
            try:
                return await request.app.state.qbit.trackers(torrent_hash)
            except QbitError:
                return []

    results = await asyncio.gather(*[fetch_trackers(t["hash"]) for t in torrents], return_exceptions=False)

    index: dict[str, list[str]] = {}
    domain_counter: dict[str, int] = {}
    for t, trackers in zip(torrents, results):
        domains: set[str] = set()
        for tr in trackers if isinstance(trackers, list) else []:
            url = tr.get("url", "")
            if _is_special_tracker(url):
                continue
            domain = _tracker_domain(url)
            if domain:
                domains.add(domain)
        if domains:
            index[t["hash"]] = sorted(domains)
            for d in domains:
                domain_counter[d] = domain_counter.get(d, 0) + 1

    return {"index": index, "domains": domain_counter}


@router.post("/torrents/add-tracker", dependencies=[Depends(require_action_guard)])
async def add_tracker(request: Request, payload: AddTrackerPayload) -> dict[str, Any]:
    hashes = validate_hashes(payload.hashes)
    tracker_url = payload.trackerUrl.strip()
    return await _add_tracker_to_hashes(request, hashes, tracker_url, allow_private=False)


@router.post("/torrents/add-tr4ker-tracker", dependencies=[Depends(require_action_guard)])
async def add_tr4ker_tracker(request: Request, payload: TorrentHashesAction) -> dict[str, Any]:
    hashes = validate_hashes(payload.hashes)
    tracker_url = TR4KER_ANNOUNCE_URL.strip()
    if not tracker_url:
        raise HTTPException(
            status_code=422,
            detail=error_detail(
                "tr4ker_tracker_not_configured",
                "Adresse d'annonce tr4ker non configurée.",
                "Renseigner TORRENT_PANEL_TR4KER_ANNOUNCE_URL dans le .env",
            ),
        )
    return await _add_tracker_to_hashes(request, hashes, tracker_url, allow_private=True)


async def _add_tracker_to_hashes(
    request: Request,
    hashes: list[str],
    tracker_url: str,
    *,
    allow_private: bool,
) -> dict[str, Any]:
    validation_error = _validate_tracker_url(tracker_url)
    if validation_error:
        raise HTTPException(status_code=422, detail=error_detail("tracker_url_invalid", validation_error, "Vérifier l'adresse"))

    torrents = await request.app.state.qbit.torrents()
    torrent_map = {t["hash"]: t for t in torrents}

    private_hashes = [h for h in hashes if torrent_map.get(h, {}).get("isPrivate", False)]
    if private_hashes and not allow_private:
        raise HTTPException(
            status_code=422,
            detail=error_detail(
                "private_torrent_selected",
                f"{len(private_hashes)} torrent(s) privé(s) détecté(s). Ajouter un tracker tiers peut enfreindre les règles.",
                "Retirer la sélection",
            ),
        )

    sem = asyncio.Semaphore(6)

    async def add_to_one(torrent_hash: str) -> str:
        async with sem:
            if torrent_hash not in torrent_map:
                return "missing"
            existing = await request.app.state.qbit.trackers(torrent_hash)
            for tr in existing if isinstance(existing, list) else []:
                if isinstance(tr, dict) and tr.get("url", "").strip() == tracker_url:
                    return "duplicate"
            try:
                await request.app.state.qbit.add_tracker(torrent_hash, tracker_url)
                return "updated"
            except QbitError:
                return "failed"

    logger.info("add-tracker: url=%s, hashes=%d", _sanitize_tracker_for_logs(tracker_url), len(hashes))
    results = await asyncio.gather(*[add_to_one(h) for h in hashes], return_exceptions=False)

    updated = results.count("updated")
    duplicates = results.count("duplicate")
    missing = results.count("missing")
    failed = results.count("failed")

    invalidate_tracker_index()

    return {
        "status": "completed",
        "updated": updated,
        "duplicates": duplicates,
        "missing": missing,
        "failed": failed,
        "total": len(hashes),
    }


@router.post("/torrents/add", dependencies=[Depends(require_action_guard)])
async def add_torrent(request: Request, payload: AddMagnet) -> dict[str, object]:
    if payload.savePath and payload.savePath not in ALLOWED_SAVE_PATHS:
        raise HTTPException(
            status_code=422,
            detail=error_detail("save_path_refused", "Chemin de sauvegarde non autorisé.", "Réessayer"),
        )

    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    for index, magnet in enumerate(payload.magnets, start=1):
        valid_magnet, reason = validate_magnet(magnet)
        if valid_magnet:
            accepted.append(valid_magnet)
        else:
            rejected.append({"line": str(index), "reason": reason or "Lien magnet invalide."})

    if not accepted:
        return {"status": "rejected", "accepted": 0, "rejected": rejected}

    tr4ker_tracker_applied = False
    if payload.addTr4kerTracker:
        tracker_url = TR4KER_ANNOUNCE_URL.strip()
        if not tracker_url:
            raise HTTPException(
                status_code=422,
                detail=error_detail(
                    "tr4ker_tracker_not_configured",
                    "Adresse d'annonce tr4ker non configurée.",
                    "Renseigner TORRENT_PANEL_TR4KER_ANNOUNCE_URL dans le .env",
                ),
            )
        validation_error = _validate_tracker_url(tracker_url)
        if validation_error:
            raise HTTPException(status_code=422, detail=error_detail("tracker_url_invalid", validation_error, "Vérifier l'adresse"))
        accepted = [_magnet_with_tracker(magnet, tracker_url) for magnet in accepted]
        tr4ker_tracker_applied = True

    try:
        await request.app.state.qbit.add_magnet(
            "\n".join(accepted),
            category=payload.category.strip(),
            tags=payload.tags.strip(),
            paused=payload.paused,
            save_path=payload.savePath.strip(),
        )
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    invalidate_tracker_index()
    return {"status": "added", "accepted": len(accepted), "rejected": rejected, "tr4kerTrackerApplied": tr4ker_tracker_applied}
