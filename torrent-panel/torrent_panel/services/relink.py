"""Relink service – reattach torrents whose data was moved to organized media folders.

The cloud-panel "rangement" moves torrent content into organized folders under
the qBittorrent download root (e.g. ``qbittorrent/Films/Movie (2021)``,
``qbittorrent/Series/Show/Saison 1``) and may rename files. qBittorrent then
reports ``missingFiles`` or re-downloads because its save path (the qBittorrent
root or a too-shallow folder) no longer matches the per-torrent folder.

The repair is: locate each torrent's files on the mount (by folder name, file
name + size, or size only since renamed files keep their size), set the
torrent's save path to that exact folder, switch content layout to
``NoSubfolder`` (the organized folders are flat) and force a recheck.
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
import time
from typing import Any

from ..config import MEDIA_MOUNT_PATH, QBIT_SAVE_PATH
from ..qbittorrent import QbitError
from .media_automation import now_iso

logger = logging.getLogger("torrent_panel.relink")

MISSING_STATES = {"missingFiles", "error"}
PAUSED_DL_STATES = {"pausedDL", "stoppedDL"}

_SCAN_CACHE_TTL = 60.0
_SCAN_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}

_LAYOUT_SUBFOLDER = "Subfolder"


def _build_renames(
    index: dict[str, Any],
    located: str,
    torrent_name: str,
    specs: list[tuple[str, int]],
    anchor_prefix: str,
) -> list[dict[str, str]]:
    """Compute filesystem operations aligning the located folder with the torrent.

    Returns a list of cloud-panel operations (``rename``, ``mkdir``, ``move``)
    so the torrent content ends up in a folder named exactly like the torrent,
    with files named exactly as qBittorrent expects (matched by size).

    A category root (``qbittorrent/Films``) is **never renamed** — when files
    sit directly in the category folder, they are moved into a new
    ``<category>/<torrent name>/`` subfolder instead.
    """
    located = located.rstrip("/")
    parent_rel = posixpath.dirname(located)
    located_basename = posixpath.basename(located)
    is_category_root = bool(anchor_prefix) and parent_rel == anchor_prefix.rstrip("/")

    ops: list[dict[str, str]] = []
    disk_files = index.get("dir_files", {}).get(located, {})
    used: set[str] = set()
    for spec_name, spec_size in specs:
        torrent_file = posixpath.basename(spec_name)
        matched: str | None = None
        for disk_name, disk_size in disk_files.items():
            if disk_name in used:
                continue
            if spec_size > 0 and disk_size == spec_size:
                matched = disk_name
                break
        if matched is None:
            for disk_name in disk_files:
                if disk_name in used:
                    continue
                if _name_tokens(disk_name) == _name_tokens(torrent_file):
                    matched = disk_name
                    break
        if matched is None:
            continue
        used.add(matched)
        if is_category_root:
            ops.append(
                {
                    "op": "move",
                    "path": located,
                    "old_name": matched,
                    "new_name": torrent_file if matched != torrent_file else "",
                    "dest": posixpath.join(located, torrent_name),
                }
            )
        elif matched != torrent_file:
            ops.append({"op": "rename", "path": located, "old_name": matched, "new_name": torrent_file})

    if is_category_root:
        if located_basename != torrent_name:
            ops.insert(0, {"op": "mkdir", "path": located, "name": torrent_name})
    elif located_basename != torrent_name:
        ops.append({"op": "rename", "path": parent_rel, "old_name": located_basename, "new_name": torrent_name})

    return ops


def _normalize_path(value: str) -> str:
    if not value:
        return ""
    return posixpath.normpath(str(value).strip().replace(os.sep, "/")).rstrip("/")


def _file_basename(name: str) -> str:
    return posixpath.basename(str(name or "")).strip()


def _skipped_entry(torrent: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "hash": str(torrent.get("hash") or "").lower(),
        "name": str(torrent.get("name") or "Torrent"),
        "category": str(torrent.get("category") or ""),
        "reason": reason,
    }


def _scan_index(mount_root: str, qbit_root: str) -> dict[str, Any]:
    """Index every file under the qBittorrent download root on the mount.

    Returns ``{"anchor_prefix": str, "files": {basename.casefold(): {dir_rel: size}},
    "sizes": {size: [dir_rel, ...]}, "dir_basenames": {basename.casefold(): [dir_rel, ...]}}``
    where ``dir_rel`` paths are relative to ``mount_root``.
    """
    now = time.monotonic()
    cache = _SCAN_CACHE
    if cache["data"] is not None and now - cache["ts"] < _SCAN_CACHE_TTL:
        return cache["data"]

    mount_root = os.path.realpath(mount_root)
    target_name = posixpath.basename(qbit_root.rstrip("/")).casefold() if qbit_root else ""

    anchor_prefix = ""
    scan_root = mount_root
    if target_name:
        for dirpath, dirnames, _files in os.walk(mount_root):
            rel = os.path.relpath(dirpath, mount_root)
            depth = 0 if rel == "." else len(rel.split(os.sep))
            if depth > 6:
                dirnames[:] = []
                continue
            for name in dirnames:
                if name.casefold() == target_name:
                    anchor_prefix = os.path.relpath(os.path.join(dirpath, name), mount_root).replace(os.sep, "/")
                    scan_root = os.path.join(dirpath, name)
                    break
            if anchor_prefix:
                break

    files: dict[str, dict[str, int]] = {}
    sizes: dict[int, list[str]] = {}
    dir_basenames: dict[str, list[str]] = {}
    dir_files: dict[str, dict[str, int]] = {}
    for dirpath, _dirnames, filenames in os.walk(scan_root):
        dir_rel = os.path.relpath(dirpath, mount_root).replace(os.sep, "/")
        dir_basenames.setdefault(posixpath.basename(dir_rel).casefold(), []).append(dir_rel)
        dir_files.setdefault(dir_rel, {})
        for filename in filenames:
            try:
                size = os.path.getsize(os.path.join(dirpath, filename))
            except OSError:
                size = -1
            files.setdefault(filename.casefold(), {})[dir_rel] = size
            dir_files[dir_rel][filename] = size
            if size > 0:
                sizes.setdefault(size, []).append(dir_rel)

    data = {
        "anchor_prefix": anchor_prefix,
        "files": files,
        "sizes": sizes,
        "dir_basenames": dir_basenames,
        "dir_files": dir_files,
    }
    cache["data"] = data
    cache["ts"] = now
    return data


def _name_tokens(value: str) -> set[str]:
    """Lowercase alphanumeric tokens of a name, for fuzzy folder matching."""
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _locate_folder(
    index: dict[str, Any],
    file_specs: list[tuple[str, int]],
    folder_hint: str,
) -> str | None:
    """Find the folder holding the torrent's data.

    Matching strategies, tried in order:
    1. Exact basename match (the "rangement" keeps the torrent's folder intact
       but nests it under the organized structure).
    2. Folder basename == torrent name (``folder_hint``), e.g. the preserved
       content path basename.
    3. Fuzzy folder-name similarity against the hint (files were renamed too).
    4. File size match anywhere in the scope (renamed files keep their size).
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

    hint_tokens = _name_tokens(hint)

    fuzzy: list[tuple[str, int]] = []
    for dir_basename, dirs in index["dir_basenames"].items():
        overlap = len(_name_tokens(dir_basename) & hint_tokens)
        if overlap > 0:
            for dir_rel in dirs:
                fuzzy.append((dir_rel, overlap))
    if fuzzy:
        best_rel, best_overlap = max(fuzzy, key=lambda item: (item[1], -len(item[0])))
        size_dir = _size_match_dir(index, file_specs, best_rel)
        if size_dir:
            return size_dir
        if best_overlap >= 2:
            return best_rel

    return _size_match_dir(index, file_specs, None)


def _size_match_dir(index: dict[str, Any], file_specs: list[tuple[str, int]], preferred: str | None) -> str | None:
    """Return the directory whose files match the torrent's file sizes best.

    Renamed files keep their size, so a size-only match can locate them even
    when the basenames differ. If ``preferred`` is given, require it to hold at
    least one size match before falling back to other candidates.
    """
    scored: dict[str, int] = {}
    for _basename, size in file_specs:
        if size <= 0:
            continue
        for dir_rel in index["sizes"].get(size, []):
            scored[dir_rel] = scored.get(dir_rel, 0) + 1

    if preferred is not None:
        if preferred in scored:
            return preferred
        if not scored:
            return None

    if not scored:
        return None
    return max(scored.items(), key=lambda item: (item[1], -len(item[0])))[0]


def _anchor_target(qbit_root: str, anchor_prefix: str, located_mount_rel: str) -> str | None:
    """Convert a located mount-relative folder into a qBittorrent absolute path.

    ``anchor_prefix`` is the mount-relative path of the qBittorrent download
    root folder (e.g. ``qbittorrent``). When found, the suffix after it is
    appended to ``qbit_root``; otherwise the located path is treated as already
    relative to ``qbit_root`` (the mount root is the qBittorrent root).
    """
    qbit_root = _normalize_path(qbit_root)
    located = located_mount_rel.rstrip("/")
    prefix = (anchor_prefix or "").rstrip("/")
    if prefix and (located == prefix or located.startswith(prefix + "/")):
        suffix = located[len(prefix):].lstrip("/")
        return qbit_root if not suffix else _normalize_path(posixpath.join(qbit_root, suffix))
    return _normalize_path(posixpath.join(qbit_root, located))


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


def _is_shallow_save_path(qbit_root: str, current: str) -> bool:
    """True when the torrent sits at the qBittorrent root or a direct subfolder.

    A torrent whose save path is the root or ``root/Films``, ``root/Series`` …
    has probably been left there by the rangement while its content was moved
    deeper; it is a relink candidate regardless of state.
    """
    if not qbit_root or not current:
        return False
    if current == qbit_root:
        return True
    return posixpath.dirname(current) == qbit_root


async def build_relink_plan(
    qbit: Any,
    *,
    hashes: list[str] | None = None,
    mount_root: str | None = None,
) -> dict[str, Any]:
    """Build the repair plan without mutating qBittorrent."""
    mount_root = mount_root or MEDIA_MOUNT_PATH
    torrents = await qbit.torrents()
    selected_hashes = {str(item).lower() for item in (hashes or [])} if hashes else None
    qbit_root = _normalize_path(QBIT_SAVE_PATH)

    index = _scan_index(mount_root, qbit_root)
    logger.info(
        "relink: mount=%s qbit_root=%s anchor=%s fichiers=%d",
        mount_root,
        qbit_root or "(non configuré)",
        index["anchor_prefix"] or "(racine du mount)",
        len(index["files"]),
    )

    by_location: dict[str, dict[str, Any]] = {}
    recheck_only: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    included = 0

    for torrent in torrents:
        torrent_hash = str(torrent.get("hash") or "").lower()
        torrent_name = str(torrent.get("name") or "Torrent")
        if not torrent_hash or not torrent.get("name"):
            continue
        if selected_hashes is not None and torrent_hash not in selected_hashes:
            continue

        current = _normalize_path(str(torrent.get("savePath") or torrent.get("save_path") or ""))
        state = str(torrent.get("state") or "")

        if selected_hashes is None:
            if state not in MISSING_STATES and state not in PAUSED_DL_STATES and not _is_shallow_save_path(qbit_root, current):
                logger.debug(
                    "relink: %s (%s) ignoré — état %s non concerné, savePath=%s",
                    torrent_name,
                    torrent_hash[:8],
                    state,
                    current,
                )
                continue

        specs = await _torrent_file_specs(qbit, torrent_hash)
        if not specs:
            logger.info("relink: %s (%s) ignoré — aucun fichier connu", torrent_name, torrent_hash[:8])
            skipped.append(_skipped_entry(torrent, "Aucun fichier connu"))
            continue

        folder_hint = _file_basename(torrent.get("contentPath") or torrent.get("name") or "")
        located = _locate_folder(index, specs, folder_hint)
        if not located:
            logger.info("relink: %s (%s) ignoré — contenu non localisé (savePath=%s)", torrent_name, torrent_hash[:8], current)
            skipped.append(_skipped_entry(torrent, "Contenu organisé non localisé"))
            continue

        if not qbit_root:
            logger.info("relink: %s (%s) ignoré — racine qBittorrent non configurée", torrent_name, torrent_hash[:8])
            skipped.append(_skipped_entry(torrent, "Sans racine qBittorrent configurée"))
            continue

        renames = _build_renames(index, located, torrent_name, specs, index["anchor_prefix"])
        anchor = index["anchor_prefix"]
        located_clean = located.rstrip("/")
        is_category_root = bool(anchor) and posixpath.dirname(located_clean) == anchor.rstrip("/")
        parent_rel = located_clean if is_category_root else (posixpath.dirname(located_clean) or located_clean)
        target = _anchor_target(qbit_root, anchor, parent_rel)
        if not target:
            logger.info("relink: %s (%s) ignoré — cible non résolue (locate=%s)", torrent_name, torrent_hash[:8], located)
            skipped.append(_skipped_entry(torrent, "Chemin cible non résolu"))
            continue

        included += 1
        entry = {
            "hash": torrent_hash,
            "name": torrent_name,
            "category": str(torrent.get("category") or ""),
            "currentPath": current,
            "targetPath": target,
            "located": located,
            "renames": renames,
        }
        if not renames and current == target:
            logger.debug("relink: %s (%s) déjà au bon endroit (%s)", torrent_name, torrent_hash[:8], target)
            recheck_only.append(entry)
            continue

        logger.info(
            "relink: %s (%s) %s → %s (%d renommage(s))",
            torrent_name,
            torrent_hash[:8],
            current,
            target,
            len(renames),
        )
        group = by_location.setdefault(
            target,
            {"location": target, "hashes": [], "names": [], "entries": []},
        )
        group["hashes"].append(torrent_hash)
        group["names"].append(entry["name"])
        group["entries"].append(entry)

    relink_count = sum(len(group["hashes"]) for group in by_location.values())
    logger.info(
        "relink: plan prêt — %d à repositionner, %d déjà alignés (recheck), %d ignorés, %d fichiers indexés",
        relink_count,
        len(recheck_only),
        len(skipped),
        len(index["files"]),
    )
    return {
        "relink": sorted(by_location.values(), key=lambda group: group["location"]),
        "recheckOnly": recheck_only,
        "skipped": skipped,
        "total": included,
        "relinkCount": relink_count,
        "recheckOnlyCount": len(recheck_only),
        "skippedCount": len(skipped),
        "layout": _LAYOUT_SUBFOLDER,
        "generatedAt": now_iso(),
    }


async def apply_relink_plan(qbit: Any, plan: dict[str, Any], *, recheck: bool = False) -> dict[str, Any]:
    """Apply a plan: rename + pause + set location/layout.

    Files are renamed to match the torrent (folder = torrent name, files = the
    torrent content names) via the cloud-panel (which has write access to the
    mount), then qBittorrent is pointed at the parent folder with the Subfolder
    layout. Torrents are left paused so the user can verify before resuming.

    No mass recheck is triggered by default: rechecking every torrent at once
    saturates disk access (FUSE mount). Pass ``recheck=True`` to force a
    ``recheck_many`` afterwards (opt-in, e.g. a single torrent).
    """
    from .cloud_panel import CloudPanelError, arrange_batch

    all_hashes = [entry["hash"] for group in plan.get("relink", []) for entry in group["entries"]]
    all_hashes.extend(entry["hash"] for entry in plan.get("recheckOnly", []))
    all_hashes = list(dict.fromkeys(all_hashes))

    failures: list[dict[str, Any]] = []
    relinked = 0
    details: list[dict[str, Any]] = []

    if all_hashes:
        try:
            await qbit.pause_many(all_hashes)
            logger.info("relink: %d torrent(s) mis en pause", len(all_hashes))
        except QbitError:
            pass

    for group in plan.get("relink", []):
        hashes = list(group["hashes"])
        entries = list(group["entries"])
        rename_ok = True

        for entry in entries:
            entry_ops = entry.get("renames") or []
            if not entry_ops:
                continue
            try:
                result = await arrange_batch(entry_ops)
                failed = int(result.get("failed", 0))
                if failed:
                    raise CloudPanelError(f"{failed} opération(s) refusée(s)")
                logger.info("relink: %d opération(s) fichier(s) pour %s", len(entry_ops), entry["name"])
            except CloudPanelError as exc:
                rename_ok = False
                failures.append({"hash": entry["hash"], "name": entry["name"], "message": exc.message})
                logger.warning("relink: échec opération %s : %s", entry["name"], exc.message)
                continue

        if not rename_ok:
            details.append({"location": group["location"], "count": len(hashes), "ok": False, "error": "Renommage refusé"})
            continue

        try:
            await qbit.set_location_many(hashes, group["location"])
            await qbit.set_content_layout_many(hashes, _LAYOUT_SUBFOLDER)
            relinked += len(hashes)
            details.append({"location": group["location"], "count": len(hashes), "ok": True})
            logger.info("relink: %d torrent(s) → %s (layout Subfolder)", len(hashes), group["location"])
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
            logger.warning("relink: échec setLocation → %s : %s", group["location"], exc.public_message)

    failed_rechecks = 0
    if recheck and all_hashes:
        try:
            await qbit.recheck_many(all_hashes)
            logger.info("relink: recheck demandé sur %d torrent(s)", len(all_hashes))
        except QbitError:
            failed_rechecks = len(all_hashes)
            logger.warning("relink: recheck impossible sur %d torrent(s)", len(all_hashes))

    return {
        "relinked": relinked,
        "paused": len(all_hashes),
        "rechecked": len(all_hashes) if recheck else 0,
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
    recheck: bool = False,
) -> dict[str, Any]:
    """Build the plan and optionally apply it to the affected torrents."""
    plan = await build_relink_plan(qbit, hashes=hashes)
    if preview or not plan["relink"]:
        return {"plan": plan, "result": None}
    result = await apply_relink_plan(qbit, plan, recheck=recheck)
    return {"plan": plan, "result": result}
