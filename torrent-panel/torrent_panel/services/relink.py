"""Relink service – reattach torrents whose data was moved to organized media folders.

The cloud-panel "rangement" moves torrent content into category folders and
renames the containing folders (e.g. ``qbittorrent/Films/Movie (2021)``,
``qbittorrent/Series/Show/Saison 1``) while preserving file names. qBittorrent
then reports ``missingFiles`` or re-downloads because its save path (category
root) no longer matches the per-torrent folder.

The repair is: locate each torrent's files on the mount, set the torrent's save
path to that exact folder, switch content layout to ``NoSubfolder`` (the
organized folders are flat) and force a recheck.
"""

from __future__ import annotations

import os
import posixpath
import time
from typing import Any

from ..config import MEDIA_MOUNT_PATH
from ..qbittorrent import QbitError
from .media_automation import now_iso

MISSING_STATES = {"missingFiles", "error"}
PAUSED_DL_STATES = {"pausedDL", "stoppedDL"}

_SCAN_CACHE_TTL = 60.0
_SCAN_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}

_LAYOUT_NO_SUBFOLDER = "NoSubfolder"


def _normalize_path(value: str) -> str:
    return posixpath.normpath(str(value or "").strip().replace(os.sep, "/")).rstrip("/")


def _file_basename(name: str) -> str:
    return posixpath.basename(str(name or "")).strip()


def _skipped_entry(torrent: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "hash": str(torrent.get("hash") or "").lower(),
        "name": str(torrent.get("name") or "Torrent"),
        "category": str(torrent.get("category") or ""),
        "reason": reason,
    }


def _category_entry(categories: dict[str, dict[str, Any]], category: str) -> dict[str, Any] | None:
    for key, item in categories.items():
        if key == category or key.strip().lower() == category.strip().lower():
            return item
    return None


def _scan_index(mount_root: str, category_save_paths: list[str]) -> dict[str, Any]:
    """Index organized folders once per cache window.

    Returns ``{"scope_dirs": [...], "files": {basename.casefold(): {dir_rel: size}}}``
    where ``dir_rel`` paths are relative to ``mount_root``.
    """
    now = time.monotonic()
    cache = _SCAN_CACHE
    if cache["data"] is not None and now - cache["ts"] < _SCAN_CACHE_TTL:
        return cache["data"]

    target_names = {posixpath.basename(path.rstrip("/")).casefold() for path in category_save_paths if path.strip()}
    mount_root = os.path.realpath(mount_root)
    scope_dirs: list[str] = []
    if target_names:
        for dirpath, dirnames, _files in os.walk(mount_root):
            rel = os.path.relpath(dirpath, mount_root)
            depth = 0 if rel == "." else len(rel.split(os.sep))
            if depth > 6:
                dirnames[:] = []
                continue
            for name in list(dirnames):
                if name.casefold() in target_names:
                    scope_dirs.append(os.path.relpath(os.path.join(dirpath, name), mount_root).replace(os.sep, "/"))

    files: dict[str, dict[str, int]] = {}
    dir_basenames: dict[str, list[str]] = {}
    for scope_rel in scope_dirs:
        scope_abs = os.path.join(mount_root, *scope_rel.split("/"))
        for dirpath, _dirnames, filenames in os.walk(scope_abs):
            dir_rel = os.path.relpath(dirpath, mount_root).replace(os.sep, "/")
            dir_basenames.setdefault(posixpath.basename(dir_rel).casefold(), []).append(dir_rel)
            for filename in filenames:
                try:
                    size = os.path.getsize(os.path.join(dirpath, filename))
                except OSError:
                    size = -1
                files.setdefault(filename.casefold(), {})[dir_rel] = size

    data = {
        "scope_dirs": sorted(set(scope_dirs)),
        "files": files,
        "dir_basenames": dir_basenames,
    }
    cache["data"] = data
    cache["ts"] = now
    return data


def _locate_folder(
    index: dict[str, Any],
    file_specs: list[tuple[str, int]],
    folder_hint: str,
) -> str | None:
    """Find the folder holding the torrent's data.

    The "rangement" keeps the torrent's downloaded folder intact but nests it
    under the category structure, so first match by that preserved folder name
    (``folder_hint``, e.g. the torrent name / content path basename). If that
    fails, fall back to matching by file name + size anywhere in the scope.
    """
    candidates: dict[str, dict[str, Any]] = {}
    for basename, size in file_specs:
        for dir_rel, dir_size in index["files"].get(basename.casefold(), {}).items():
            candidate = candidates.setdefault(dir_rel, {"names": 0, "size_ok": 0})
            candidate["names"] += 1
            if size > 0 and dir_size == size:
                candidate["size_ok"] += 1

    def _score(pair: tuple[str, dict[str, Any]]) -> tuple[int, int, int]:
        dir_rel, candidate = pair
        return (candidate["size_ok"], candidate["names"], len(dir_rel))

    hint = (folder_hint or "").strip().casefold()
    if hint:
        hinted: list[tuple[str, dict[str, Any]]] = []
        for dir_rel in index["dir_basenames"].get(hint, []):
            candidate = candidates.get(dir_rel, {"names": 0, "size_ok": 0})
            hinted.append((dir_rel, candidate))
        if hinted:
            best = max(hinted, key=_score)
            if best[1]["names"] > 0:
                return best[0]

    if candidates:
        return max(candidates.items(), key=_score)[0]
    return None


def _anchor_target(category_qbit_path: str, scope_mount_rel: str, located_mount_rel: str) -> str | None:
    """Convert a located mount-relative folder into a qBittorrent absolute path."""
    category_path = _normalize_path(category_qbit_path)
    scope = scope_mount_rel.rstrip("/")
    located = located_mount_rel.rstrip("/")
    if located == scope:
        return category_path
    if located.startswith(scope + "/"):
        suffix = located[len(scope):].lstrip("/")
        return _normalize_path(posixpath.join(category_path, suffix))
    return None


async def _torrent_file_specs(qbit: Any, torrent_hash: str) -> list[tuple[str, int]]:
    try:
        payload = await qbit.files(torrent_hash)
    except QbitError:
        return []
    specs: list[tuple[str, int]] = []
    for item in payload if isinstance(payload, list) else []:
        basename = _file_basename(item.get("name") if isinstance(item, dict) else "")
        if basename:
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            specs.append((basename, size))
    return specs


async def build_relink_plan(
    qbit: Any,
    *,
    hashes: list[str] | None = None,
    mount_root: str | None = None,
) -> dict[str, Any]:
    """Build the repair plan without mutating qBittorrent."""
    mount_root = mount_root or MEDIA_MOUNT_PATH
    torrents = await qbit.torrents()
    categories = await qbit.categories()
    selected_hashes = {str(item).lower() for item in (hashes or [])} if hashes else None

    category_save_paths = [str(item.get("savePath") or "") for item in categories.values() if isinstance(item, dict)]
    index = _scan_index(mount_root, category_save_paths)

    by_location: dict[str, dict[str, Any]] = {}
    recheck_only: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    included = 0

    for torrent in torrents:
        torrent_hash = str(torrent.get("hash") or "").lower()
        if not torrent_hash or not torrent.get("name"):
            continue
        if selected_hashes is not None and torrent_hash not in selected_hashes:
            continue

        category = str(torrent.get("category") or "").strip()
        category_info = _category_entry(categories, category) if category else None
        if not category_info or not (category_info.get("savePath") or ""):
            skipped.append(_skipped_entry(torrent, "Sans catégorie ou chemin configuré"))
            continue

        category_qbit_path = str(category_info["savePath"]).strip()
        current = _normalize_path(str(torrent.get("savePath") or torrent.get("save_path") or ""))
        state = str(torrent.get("state") or "")

        if selected_hashes is None and state not in MISSING_STATES and state not in PAUSED_DL_STATES and current != _normalize_path(category_qbit_path):
            continue

        specs = await _torrent_file_specs(qbit, torrent_hash)
        if not specs:
            skipped.append(_skipped_entry(torrent, "Aucun fichier connu"))
            continue

        folder_hint = _file_basename(torrent.get("contentPath") or torrent.get("name") or "")
        located = _locate_folder(index, specs, folder_hint)
        if not located:
            skipped.append(_skipped_entry(torrent, "Contenu organisé non localisé"))
            continue

        scope_dir = next(
            (scope for scope in index["scope_dirs"] if located == scope.rstrip("/") or located.startswith(scope.rstrip("/") + "/")),
            None,
        )
        if scope_dir is None:
            skipped.append(_skipped_entry(torrent, "Dossier catégorie non résolu"))
            continue

        target = _anchor_target(category_qbit_path, scope_dir, located)
        if not target:
            skipped.append(_skipped_entry(torrent, "Chemin cible non résolu"))
            continue

        included += 1
        entry = {
            "hash": torrent_hash,
            "name": str(torrent.get("name") or "Torrent"),
            "category": category,
            "currentPath": current,
            "targetPath": target,
        }
        if current == target:
            recheck_only.append(entry)
            continue

        group = by_location.setdefault(
            target,
            {"location": target, "hashes": [], "names": [], "entries": []},
        )
        group["hashes"].append(torrent_hash)
        group["names"].append(entry["name"])
        group["entries"].append(entry)

    return {
        "relink": sorted(by_location.values(), key=lambda group: group["location"]),
        "recheckOnly": recheck_only,
        "skipped": skipped,
        "total": included,
        "relinkCount": sum(len(group["hashes"]) for group in by_location.values()),
        "recheckOnlyCount": len(recheck_only),
        "skippedCount": len(skipped),
        "layout": _LAYOUT_NO_SUBFOLDER,
        "generatedAt": now_iso(),
    }


async def apply_relink_plan(qbit: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Apply a plan: pause, set location + layout, then force recheck. Never raises.

    Torrents are left paused so the user can verify before resuming seeding.
    """
    all_hashes = [entry["hash"] for group in plan.get("relink", []) for entry in group["entries"]]
    all_hashes.extend(entry["hash"] for entry in plan.get("recheckOnly", []))
    all_hashes = list(dict.fromkeys(all_hashes))

    failures: list[dict[str, Any]] = []
    relinked = 0
    details: list[dict[str, Any]] = []

    if all_hashes:
        try:
            await qbit.pause_many(all_hashes)
        except QbitError:
            pass

    for group in plan.get("relink", []):
        hashes = list(group["hashes"])
        try:
            await qbit.set_location_many(hashes, group["location"])
            await qbit.set_content_layout_many(hashes, _LAYOUT_NO_SUBFOLDER)
            relinked += len(hashes)
            details.append({"location": group["location"], "count": len(hashes), "ok": True})
        except QbitError as exc:
            details.append({"location": group["location"], "count": len(hashes), "ok": False, "error": exc.public_message})
            failures.extend(
                {
                    "hash": torrent_hash,
                    "name": name,
                    "message": exc.public_message,
                }
                for torrent_hash, name in zip(hashes, group["names"])
            )

    failed_rechecks = 0
    if all_hashes:
        try:
            await qbit.recheck_many(all_hashes)
        except QbitError:
            failed_rechecks = len(all_hashes)

    return {
        "relinked": relinked,
        "paused": len(all_hashes),
        "rechecked": len(all_hashes),
        "recheckFailed": failed_rechecks,
        "failed": len(failures),
        "skipped": plan.get("skippedCount", 0),
        "details": details,
        "failures": failures,
    }


async def relink_missing(
    qbit: Any,
    *,
    hashes: list[str] | None = None,
    preview: bool = False,
) -> dict[str, Any]:
    """Build the plan and optionally apply it to the affected torrents."""
    plan = await build_relink_plan(qbit, hashes=hashes)
    if preview or not plan["relink"]:
        return {"plan": plan, "result": None}
    result = await apply_relink_plan(qbit, plan)
    return {"plan": plan, "result": result}
