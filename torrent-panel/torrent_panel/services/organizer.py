"""Safe Jellyfin organization driven by qBittorrent.

Unlike the legacy cloud-panel organizer, this workflow never moves a file
behind qBittorrent's back.  qBittorrent renames every owned path itself, then
moves the torrent to the Films or Series root and verifies it before any
previously-active torrent may resume.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import re
import time
from pathlib import Path
from typing import Any

from ..qbittorrent import QbitError

logger = logging.getLogger("torrent_panel.organizer")

_SEASON_RE = re.compile(
    r"(?i)(?:^|[\s._\-/])(?:S(?:eason)?|Saison)[\s._-]*(\d{1,2})(?=$|[\s._\-/E])"
)
_EPISODE_RE = re.compile(r"(?i)(?:^|[\s._\-/])S(\d{1,2})E\d{1,3}\b")
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".webm"}
_ACTIVE_SEED_STATES = {"uploading", "stalledUP", "forcedUP", "queuedUP", "checkingUP"}
_CHECKING_STATES = {"checkingUP", "checkingDL", "queuedForChecking", "checkingResumeData"}
_PAUSED_STATES = {"pausedUP", "pausedDL", "stoppedUP", "stoppedDL", "missingFiles", "error"}
_CATEGORY_NAMES = {"Films", "Series"}


def _normalize_qbit_root(value: str) -> str:
    root = str(value or "").strip().replace("\\", "/").rstrip("/")
    if posixpath.basename(root).casefold() in {name.casefold() for name in _CATEGORY_NAMES}:
        root = posixpath.dirname(root)
    return root


def _supports_path_rename(version: str) -> bool:
    try:
        parts = tuple(int(part) for part in str(version).strip().split(".")[:2])
    except ValueError:
        return False
    return parts >= (2, 8)


def _clean_title(value: str) -> str:
    words = [part for part in re.split(r"[\s._-]+", value.strip(" ._-")) if part]
    return " ".join(part if part.isupper() and len(part) <= 4 else part[:1].upper() + part[1:].lower() for part in words)


def _safe_relative(value: str) -> str:
    normalized = posixpath.normpath(str(value or "").replace("\\", "/")).lstrip("/")
    if not normalized or normalized == "." or normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise ValueError("Chemin torrent invalide")
    return normalized


def _common_root(paths: list[str]) -> str:
    first_parts = [path.split("/", 1) for path in paths]
    if paths and all(len(parts) == 2 for parts in first_parts):
        candidate = first_parts[0][0]
        if all(parts[0] == candidate for parts in first_parts):
            return candidate
    return ""


def _strip_common_root(path: str, root: str) -> str:
    if root and path.startswith(root + "/"):
        return path[len(root) + 1 :]
    return path


def _season_number(value: str) -> int | None:
    match = _EPISODE_RE.search(value) or _SEASON_RE.search(value)
    return int(match.group(1)) if match else None


def _series_title(value: str) -> str | None:
    match = _EPISODE_RE.search(value) or _SEASON_RE.search(value)
    if not match:
        return None
    title = _clean_title(value[: match.start()])
    return title or None


def _movie_identity(value: str) -> tuple[str, str] | None:
    match = _YEAR_RE.search(value)
    if not match:
        return None
    title = _clean_title(value[: match.start()])
    return (title, match.group(1)) if title else None


def _is_complete(torrent: dict[str, Any]) -> bool:
    try:
        return float(torrent.get("progress", 0)) >= 1 or (
            int(torrent.get("remaining", torrent.get("amount_left", 1)) or 0) == 0
            and int(torrent.get("size", 0) or 0) > 0
        )
    except (TypeError, ValueError):
        return False


def _torrent_plan(torrent: dict[str, Any], files: list[dict[str, Any]], qbit_root: str) -> dict[str, Any] | None:
    torrent_name = str(torrent.get("name") or "").strip()
    torrent_hash = str(torrent.get("hash") or "").lower()
    paths = [_safe_relative(str(item.get("name") or "")) for item in files if isinstance(item, dict) and item.get("name")]
    if not torrent_name or not torrent_hash or not paths:
        return None

    media_paths = [path for path in paths if posixpath.splitext(path)[1].lower() in _VIDEO_EXTENSIONS]
    detection_values = [torrent_name, *media_paths]
    series_value = next((value for value in detection_values if _season_number(value) is not None), None)
    movie = None if series_value else next((_movie_identity(value) for value in detection_values if _movie_identity(value)), None)
    if series_value:
        kind = "series"
        title = _series_title(torrent_name) or next(
            (_series_title(posixpath.basename(path)) for path in media_paths if _series_title(posixpath.basename(path))),
            None,
        ) or _series_title(series_value)
        if not title:
            return None
        label = title
        location = posixpath.join(qbit_root, "Series")
    elif movie:
        kind = "film"
        label = f"{movie[0]} ({movie[1]})"
        location = posixpath.join(qbit_root, "Films")
    else:
        return None

    common_root = _common_root(paths)
    default_season = _season_number(torrent_name) or (_season_number(series_value) if series_value else None)
    operations: list[dict[str, str]] = []
    targets: set[str] = set()
    for old_path in paths:
        remainder = _strip_common_root(old_path, common_root)
        if kind == "film":
            new_path = posixpath.join(label, remainder)
        else:
            season = _season_number(old_path) or default_season
            if season is None:
                return None
            parts = remainder.split("/")
            if len(parts) > 1 and _season_number(parts[0]) is not None:
                remainder = "/".join(parts[1:])
            new_path = posixpath.join(label, f"Saison {season}", remainder)
        new_path = _safe_relative(new_path)
        if new_path in targets:
            raise ValueError(f"Collision de destination dans {torrent_name}")
        targets.add(new_path)
        if old_path != new_path:
            operations.append({"oldPath": old_path, "newPath": new_path})

    # A common release folder can usually be renamed once instead of issuing
    # two Web API calls per file. Multi-season packs fall back to file-level
    # moves because their internal Season directories also change names.
    if common_root and len(operations) == len(paths):
        folder_target: str | None = None
        folder_compatible = True
        for operation in operations:
            remainder = _strip_common_root(operation["oldPath"], common_root)
            suffix = "/" + remainder
            if not operation["newPath"].endswith(suffix):
                folder_compatible = False
                break
            candidate = operation["newPath"][: -len(suffix)]
            if folder_target is None:
                folder_target = candidate
            elif candidate != folder_target:
                folder_compatible = False
                break
        if folder_compatible and folder_target and folder_target != common_root:
            operations = [{"type": "folder", "oldPath": common_root, "newPath": folder_target}]

    current_path = str(torrent.get("savePath") or torrent.get("save_path") or "").rstrip("/")
    return {
        "hash": torrent_hash,
        "name": torrent_name,
        "kind": kind,
        "folder": label,
        "currentPath": current_path,
        "targetPath": location,
        "operations": operations,
        "targetFiles": sorted(targets),
        "fileCount": len(paths),
        "initialState": str(torrent.get("state") or ""),
        "layout": "Chemins relatifs qBittorrent",
        "resumeAfterVerify": str(torrent.get("state") or "") in _ACTIVE_SEED_STATES,
        "alreadyOrganized": not operations and current_path == location,
    }


def _unassociated_files(mount_root: str, qbit_root: str, signatures: set[tuple[str, int]]) -> list[dict[str, str]]:
    """Find disk files not represented by qBittorrent, capped for predictable previews."""
    mount = os.path.realpath(mount_root)
    anchor_name = posixpath.basename(qbit_root.rstrip("/")).casefold()
    anchor = mount
    try:
        direct = next((entry.path for entry in os.scandir(mount) if entry.is_dir() and entry.name.casefold() == anchor_name), None)
        if direct:
            anchor = direct
    except OSError:
        return []
    warnings: list[dict[str, str]] = []
    scanned = 0
    for directory, _dirs, filenames in os.walk(anchor):
        for filename in filenames:
            scanned += 1
            if scanned > 10_000:
                warnings.append({"name": "Analyse du stockage", "reason": "Analyse arrêtée après 10 000 fichiers"})
                return warnings
            path = os.path.join(directory, filename)
            try:
                signature = (filename.casefold(), os.path.getsize(path))
            except OSError:
                continue
            if signature in signatures:
                continue
            relative = os.path.relpath(path, anchor).replace(os.sep, "/")
            warnings.append({"name": relative, "reason": "Fichier présent mais non associé à qBittorrent"})
            if len(warnings) >= 100:
                warnings.append({"name": "Stockage", "reason": "Autres fichiers non associés non affichés"})
                return warnings
    return warnings


async def build_organization_plan(
    qbit: Any,
    qbit_root: str,
    hashes: list[str] | None = None,
    *,
    mount_root: str | None = None,
) -> dict[str, Any]:
    root = _normalize_qbit_root(qbit_root)
    if not root:
        return {"entries": [], "warnings": [{"reason": "Racine qBittorrent non configurée"}], "count": 0}
    if hasattr(qbit, "webapi_version"):
        try:
            webapi_version = await qbit.webapi_version()
        except QbitError:
            return {
                "entries": [],
                "warnings": [{"reason": "Version Web API qBittorrent impossible à vérifier — rangement refusé"}],
                "count": 0,
                "warningCount": 1,
            }
        if not _supports_path_rename(webapi_version):
            return {
                "entries": [],
                "warnings": [{"reason": f"qBittorrent Web API {webapi_version or 'inconnue'} incompatible (2.8 minimum)"}],
                "count": 0,
                "warningCount": 1,
            }
    selected = {str(value).lower() for value in hashes or []}
    candidates: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    signatures: set[tuple[str, int]] = set()
    torrents = [
        torrent
        for torrent in await qbit.torrents()
        if not selected or str(torrent.get("hash") or "").lower() in selected
    ]
    file_limit = asyncio.Semaphore(6)

    async def fetch_files(torrent_hash: str) -> list[dict[str, Any]]:
        async with file_limit:
            payload = await qbit.files(torrent_hash)
            return payload if isinstance(payload, list) else []

    file_payloads = await asyncio.gather(
        *(fetch_files(str(torrent.get("hash") or "").lower()) for torrent in torrents)
    )
    for torrent, files in zip(torrents, file_payloads):
        torrent_hash = str(torrent.get("hash") or "").lower()
        for item in files if isinstance(files, list) else []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            signatures.add((posixpath.basename(str(item["name"])).casefold(), size))
        if not _is_complete(torrent):
            warnings.append({"hash": torrent_hash, "name": str(torrent.get("name") or "Torrent"), "reason": "Torrent incomplet — rangement refusé"})
            continue
        try:
            entry = _torrent_plan(torrent, files if isinstance(files, list) else [], root)
        except ValueError as exc:
            warnings.append({"hash": torrent_hash, "name": str(torrent.get("name") or "Torrent"), "reason": str(exc)})
            continue
        if entry is None:
            warnings.append({"hash": torrent_hash, "name": str(torrent.get("name") or "Torrent"), "reason": "Film ou série non identifié"})
            continue
        candidates.append(entry)
    owners: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in candidates:
        for target_file in entry["targetFiles"]:
            owners.setdefault((entry["targetPath"], target_file.casefold()), []).append(entry)
    colliding_hashes: set[str] = set()
    for (_location, target_file), target_owners in owners.items():
        distinct = {entry["hash"] for entry in target_owners}
        if len(distinct) <= 1:
            continue
        colliding_hashes.update(distinct)
        names = ", ".join(sorted({entry["name"] for entry in target_owners}))
        warnings.append({"name": names, "reason": f"Collision de destination ({target_file}) — rangement refusé"})
    entries = [entry for entry in candidates if not entry["alreadyOrganized"] and entry["hash"] not in colliding_hashes]
    if mount_root and not selected:
        warnings.extend(_unassociated_files(mount_root, root, signatures))
    return {"entries": entries, "warnings": warnings, "count": len(entries), "warningCount": len(warnings)}


def _temporary_path(torrent_hash: str, index: int, old_path: str) -> str:
    basename = posixpath.basename(old_path)
    return posixpath.join(".__dashboard_organize__", torrent_hash[:12], f"{index:05d}-{basename}")


async def _rename_path(qbit: Any, torrent_hash: str, path_type: str, old_path: str, new_path: str) -> None:
    if path_type == "folder":
        await qbit.rename_folder(torrent_hash, old_path, new_path)
    else:
        await qbit.rename_file(torrent_hash, old_path, new_path)


async def _wait_until_paused(qbit: Any, torrent_hash: str, timeout_seconds: float = 10.0) -> None:
    """Wait for qBittorrent to finish stopping before path mutations."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        torrents = await qbit.torrents()
        torrent = next((item for item in torrents if str(item.get("hash") or "").lower() == torrent_hash), None)
        if torrent is None:
            raise QbitError(404, "Torrent introuvable après sa mise en pause.")
        if str(torrent.get("state") or "") in _PAUSED_STATES:
            return
        await asyncio.sleep(0.25)
    raise QbitError(409, "qBittorrent n'a pas confirmé la mise en pause — rangement annulé.")


async def _rollback(
    qbit: Any,
    entry: dict[str, Any],
    renamed: list[tuple[str, str, str]],
    location_changed: bool,
) -> list[str]:
    errors: list[str] = []
    if location_changed and entry["currentPath"]:
        try:
            await qbit.set_location_many([entry["hash"]], entry["currentPath"])
        except QbitError as exc:
            errors.append(f"localisation: {exc.public_message}")
    for path_type, current, original in reversed(renamed):
        try:
            await _rename_path(qbit, entry["hash"], path_type, current, original)
        except QbitError as exc:
            errors.append(f"{current}: {exc.public_message}")
    return errors


async def apply_organization_plan(qbit: Any, plan: dict[str, Any], resume_manager: "VerifiedResumeManager") -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    organized = 0
    for entry in plan.get("entries", []):
        torrent_hash = entry["hash"]
        renamed: list[tuple[str, str, str]] = []
        location_changed = False
        try:
            await qbit.pause_many([torrent_hash])
            await _wait_until_paused(qbit, torrent_hash)
            temporary: list[tuple[str, str, str, str]] = []
            for index, operation in enumerate(entry["operations"]):
                path_type = str(operation.get("type") or "file")
                temp_path = _temporary_path(torrent_hash, index, operation["oldPath"])
                await _rename_path(qbit, torrent_hash, path_type, operation["oldPath"], temp_path)
                renamed.append((path_type, temp_path, operation["oldPath"]))
                temporary.append((path_type, temp_path, operation["newPath"], operation["oldPath"]))
            for index, (path_type, temp_path, new_path, old_path) in enumerate(temporary):
                await _rename_path(qbit, torrent_hash, path_type, temp_path, new_path)
                renamed[index] = (path_type, new_path, old_path)
            if entry["currentPath"] != entry["targetPath"]:
                await qbit.set_location_many([torrent_hash], entry["targetPath"])
                location_changed = True
            await qbit.recheck_many([torrent_hash])
            if entry["resumeAfterVerify"]:
                resume_manager.track(torrent_hash, force_start=entry["initialState"] == "forcedUP")
            organized += 1
            results.append({"hash": torrent_hash, "name": entry["name"], "success": True, "status": "verification"})
        except QbitError as exc:
            rollback_errors = await _rollback(qbit, entry, renamed, location_changed)
            results.append(
                {
                    "hash": torrent_hash,
                    "name": entry["name"],
                    "success": False,
                    "error": exc.public_message,
                    "rollback": "ok" if not rollback_errors else "incomplet",
                    "rollbackErrors": rollback_errors,
                }
            )
    return {"organized": organized, "failed": len(results) - organized, "results": results, "warnings": plan.get("warnings", [])}


class VerifiedResumeManager:
    """Resume only torrents that were active before organization and verify at 100 %."""

    def __init__(self, qbit: Any, state_path: Path, poll_seconds: float = 10.0):
        self._qbit = qbit
        self._state_path = state_path
        self._poll_seconds = max(2.0, poll_seconds)
        self._pending: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._pending = {str(key): value for key, value in payload.items() if isinstance(value, dict)}
        except (OSError, ValueError):
            self._pending = {}

    def _save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._pending, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self._state_path)

    def track(self, torrent_hash: str, *, force_start: bool = False) -> None:
        self._pending[torrent_hash] = {"forceStart": force_start, "notBefore": time.time() + 5, "seenChecking": False}
        self._save()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="torrent-panel-verified-resume")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def check_once(self) -> None:
        if not self._pending:
            return
        torrents = {str(item.get("hash") or "").lower(): item for item in await self._qbit.torrents()}
        changed = False
        for torrent_hash, pending in list(self._pending.items()):
            torrent = torrents.get(torrent_hash)
            if torrent is None:
                continue
            state = str(torrent.get("state") or "")
            if state in _CHECKING_STATES:
                pending["seenChecking"] = True
                changed = True
                continue
            try:
                progress = float(torrent.get("progress", 0))
            except (TypeError, ValueError):
                progress = 0
            if time.time() < float(pending.get("notBefore", 0)):
                continue
            if progress < 1:
                continue
            await self._qbit.resume_many([torrent_hash])
            if pending.get("forceStart"):
                await self._qbit.set_force_start_many([torrent_hash], True)
            del self._pending[torrent_hash]
            changed = True
        if changed:
            self._save()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                await self.check_once()
            except Exception:
                logger.exception("Verified resume check failed")

    def snapshot(self) -> dict[str, Any]:
        return {"pending": len(self._pending), "hashes": [value[:8] for value in self._pending]}
