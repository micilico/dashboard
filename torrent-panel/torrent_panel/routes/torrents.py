"""Torrent CRUD routes."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request

from .csrf_guard import require_action_guard
from ..config import ALLOWED_SAVE_PATHS, HASH_RE
from ..models import (
    AddMagnet,
    AddTrackerPayload,
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

logger = logging.getLogger("torrent_panel.routes.trackers")

_TRACKER_SPECIAL_PREFIXES = ("** [DHT]", "** [PeX]", "** [LSD]", "** [Metadata]")

_TRACKER_INDEX_CACHE: dict[str, Any] = {"data": None, "ts": 0.0}
_TRACKER_CACHE_TTL = 60
_TRACKER_CONCURRENCY = 6

TRACKER_VALID_SCHEMES = ("http://", "https://", "udp://", "ws://", "wss://")
_URL_IN_TEXT_RE = re.compile(r"(?:https?|udp|wss?)://\S+", re.IGNORECASE)

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
            public_tracker["url"] = raw_url if _is_special_tracker(raw_url) else _tracker_domain(raw_url)
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
    now = time.monotonic()
    cache = _TRACKER_INDEX_CACHE
    if cache["data"] is not None and now - cache["ts"] < _TRACKER_CACHE_TTL:
        return cache["data"]

    try:
        torrents = await request.app.state.qbit.torrents()
    except QbitError as exc:
        raise qbit_error_response(exc) from exc

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

    payload = {"index": index, "domains": domain_counter}
    cache["data"] = payload
    cache["ts"] = now
    return payload


@router.post("/torrents/add-tracker", dependencies=[Depends(require_action_guard)])
async def add_tracker(request: Request, payload: AddTrackerPayload) -> dict[str, Any]:
    hashes = validate_hashes(payload.hashes)
    tracker_url = payload.trackerUrl.strip()
    validation_error = _validate_tracker_url(tracker_url)
    if validation_error:
        raise HTTPException(status_code=422, detail=error_detail("tracker_url_invalid", validation_error, "Vérifier l'adresse"))

    torrents = await request.app.state.qbit.torrents()
    torrent_map = {t["hash"]: t for t in torrents}

    private_hashes = [h for h in hashes if torrent_map.get(h, {}).get("isPrivate", False)]
    if private_hashes:
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

    _TRACKER_INDEX_CACHE["ts"] = 0

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
