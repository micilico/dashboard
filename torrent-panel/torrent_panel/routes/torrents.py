"""Torrent CRUD routes."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .csrf_guard import require_action_guard
from ..config import ALLOWED_SAVE_PATHS, HASH_RE
from ..models import (
    AddMagnet,
    DeleteTorrent,
    ForceStartTorrent,
    TorrentCategoryUpdate,
    TorrentHashesAction,
    TorrentRateLimitUpdate,
    TorrentSequentialUpdate,
    TorrentTagsUpdate,
)
from ..qbittorrent import QbitError
from common import error_detail

router = APIRouter()


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


@router.post("/torrents/set-sequential", dependencies=[Depends(require_action_guard)])
async def set_torrent_sequential(request: Request, payload: TorrentSequentialUpdate) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await request.app.state.qbit.set_sequential_download_many(hashes, payload.enabled)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "sequential_updated", "count": len(hashes), "enabled": payload.enabled}


@router.get("/torrents/{torrent_hash}/trackers")
async def torrent_trackers(request: Request, torrent_hash: str) -> dict[str, Any]:
    try:
        return {"trackers": await request.app.state.qbit.trackers(validate_hash(torrent_hash))}
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@router.get("/torrents/{torrent_hash}/files")
async def torrent_files(request: Request, torrent_hash: str) -> dict[str, Any]:
    try:
        return {"files": await request.app.state.qbit.files(validate_hash(torrent_hash))}
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


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
    return {"status": "added", "accepted": len(accepted), "rejected": rejected}
