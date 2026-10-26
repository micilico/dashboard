"""Relink service – reattach torrents whose data was moved to organized media folders.

The cloud-panel "rangement" moves torrent content into organized folders under
the qBittorrent download root (e.g. ``qbittorrent/Films/Movie (2021)``,
``qbittorrent/Series/Show/Saison 1``) and may rename files. qBittorrent then
reports ``missingFiles`` or re-downloads because its save path (the qBittorrent
root or a too-shallow folder) no longer matches the per-torrent folder.

Two repair modes are proposed:

``in_place``
    Point qBittorrent at the folder that actually holds the content. This is
    the fastest mode: no file is moved when the folder is already named like
    the torrent.

``category``
    Move the torrent folder into its library category (``qbittorrent/Jeux``,
    ``Films``, ``Series``, ``Livres``) with a single folder operation, then
    point qBittorrent there. When the content is already in the right category
    this is a no-op move.

A targeted repair (one or a few hashes) first tries a bounded fast locator and
never walks the whole rclone mount when the folder is found at its expected
place.
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import re
import threading
import time
from typing import Any

from ..config import MEDIA_MOUNT_PATH, QBIT_SAVE_PATH
from ..qbittorrent import QbitError
from .media_automation import now_iso
from .organizer import _CATEGORY_BY_KIND, _classify_torrent

logger = logging.getLogger("torrent_panel.relink")

MISSING_STATES = {"missingFiles", "error"}
PAUSED_DL_STATES = {"pausedDL", "stoppedDL"}
REPAIR_MODES = {"in_place", "category"}

_SCAN_CACHE_TTL = 60.0
_SCAN_CACHE: dict[str, Any] = {"ts": 0.0, "data": None, "key": None}
_SCAN_LOCK = threading.Lock()

_LAYOUT_SUBFOLDER = "Subfolder"
_LAYOUT_NO_SUBFOLDER = "NoSubfolder"

_KNOWN_LIBRARY_CATEGORIES = {value.casefold() for value in _CATEGORY_BY_KIND.values()}
# Categories used by the download automation, not by the media library.  They
# must never drive the category selected by a repair.
_GENERIC_CATEGORIES = {
    "default",
    "incomplete",
    "lidarr",
    "prowlarr",
    "radarr",
    "readarr",
    "sonarr",
    "temp",
    "torrents",
}


def _normalize_path(value: str) -> str:
    if not value:
        return ""
    return posixpath.normpath(str(value).strip().replace(os.sep, "/")).rstrip("/")


def _file_basename(name: str) -> str:
    return posixpath.basename(str(name or "").strip().replace("\\", "/"))


def _safe_folder_name(value: str) -> str:
    """Return a single safe folder name, or an empty string."""
    candidate = str(value or "").strip().replace("\\", "/")
    if not candidate or "/" in candidate or candidate in {".", ".."} or len(candidate) > 160:
        return ""
    return candidate


def _name_tokens(value: str) -> set[str]:
    """Lowercase alphanumeric tokens of a name, for fuzzy folder matching."""
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _folder_matches_hint(name: str, *hints: str) -> bool:
    """True when a folder name is a plausible root for one of the hints.

    Only strong matches are accepted: equal names, one name extending the
    other after a separator (``Blue Prince (v1.0)`` vs ``Blue Prince``), or
    identical token sets.  A loose token overlap is left to the full scan.
    """
    candidate = str(name or "").strip().casefold()
    if not candidate:
        return False
    candidate_tokens = _name_tokens(candidate)
    for raw_hint in hints:
        hint = str(raw_hint or "").strip().casefold()
        if not hint:
            continue
        if candidate == hint:
            return True
        for separator in (" ", ".", "-", "("):
            if candidate.startswith(hint + separator) or hint.startswith(candidate + separator):
                return True
        hint_tokens = _name_tokens(hint)
        if candidate_tokens and candidate_tokens == hint_tokens:
            return True
    return False


def _path_within(path: str, parent: str) -> bool:
    if not parent:
        return False
    value = str(path or "").rstrip("/").casefold()
    root = str(parent or "").rstrip("/").casefold()
    return value == root or value.startswith(root + "/")


def _mount_rel_to_abs(mount_root: str, rel: str) -> str:
    cleaned = str(rel or "").strip("/")
    mount = os.path.realpath(mount_root)
    if not cleaned:
        return mount
    return os.path.join(mount, *cleaned.split("/"))


def _resolve_anchor_prefix(mount_root: str, qbit_root: str) -> str:
    """Find the mount-relative folder that maps to the qBittorrent root.

    The direct child is checked first (the common case), then a shallow walk
    is used as a fallback.  No deep traversal is done here.
    """
    mount = os.path.realpath(mount_root)
    target = posixpath.basename(_normalize_path(qbit_root)).casefold()
    if not target:
        return ""
    try:
        with os.scandir(mount) as entries:
            for entry in entries:
                if entry.is_dir() and entry.name.casefold() == target:
                    return entry.name
    except OSError:
        pass
    if posixpath.basename(mount).casefold() == target:
        return ""
    for dirpath, dirnames, _files in os.walk(mount):
        rel = os.path.relpath(dirpath, mount)
        depth = 0 if rel == "." else len(rel.split(os.sep))
        if depth > 6:
            dirnames[:] = []
            continue
        for name in dirnames:
            if name.casefold() == target:
                return os.path.relpath(os.path.join(dirpath, name), mount).replace(os.sep, "/")
    return ""


def _list_dir_files(mount_root: str, rel: str) -> dict[str, int]:
    """List the direct files of one directory with their sizes."""
    result: dict[str, int] = {}
    try:
        with os.scandir(_mount_rel_to_abs(mount_root, rel)) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        result[entry.name] = entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        return {}
    return result


def _list_subdirs(mount_root: str, rel: str) -> list[str]:
    result: list[str] = []
    try:
        with os.scandir(_mount_rel_to_abs(mount_root, rel)) as entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        result.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return []
    return result


def _files_match_specs(disk_files: dict[str, int], specs: list[tuple[str, int]]) -> bool:
    """Conservative signature check used by the fast locator."""
    if not disk_files or not specs:
        return False
    by_name = {name.casefold(): size for name, size in disk_files.items()}
    sizes = {size for size in disk_files.values() if size > 0}
    name_matches = 0
    size_matches = 0
    for spec_name, spec_size in specs:
        target = posixpath.basename(spec_name).casefold()
        known_size = by_name.get(target)
        if known_size is not None and (spec_size <= 0 or known_size == spec_size):
            name_matches += 1
        elif spec_size > 0 and spec_size in sizes:
            size_matches += 1
    return name_matches >= 1 or size_matches >= 2


def _has_subdir_match(mount_root: str, rel: str, specs: list[tuple[str, int]], limit: int = 32) -> bool:
    for index, name in enumerate(_list_subdirs(mount_root, rel)):
        if index >= limit:
            break
        if _files_match_specs(_list_dir_files(mount_root, posixpath.join(rel, name)), specs):
            return True
    return False


def _locate_folder_fast(
    mount_root: str,
    anchor_prefix: str,
    category_rel: str,
    torrent_name: str,
    folder_hint: str,
    specs: list[tuple[str, int]],
) -> tuple[str, dict[str, int]] | None:
    """Locate a torrent folder without walking the whole mount.

    Only strong name matches (content path or torrent name) are accepted.
    Direct file signatures are checked first, then one level of subdirectories
    so that games with an internal data folder are still found.  Anything
    ambiguous falls back to the full scan.
    """
    hints: list[str] = []
    for value in (folder_hint, torrent_name):
        cleaned = _file_basename(value)
        if cleaned and cleaned not in hints:
            hints.append(cleaned)

    scopes: list[str] = []
    for scope in (category_rel, anchor_prefix):
        value = str(scope or "").rstrip("/")
        if value and value not in scopes:
            scopes.append(value)
    if not scopes:
        scopes.append("")

    candidates: list[str] = []
    for scope in scopes:
        for hint in hints:
            rel = posixpath.join(scope, hint) if scope else hint
            if rel not in candidates:
                candidates.append(rel)

    for rel in candidates:
        if not os.path.isdir(_mount_rel_to_abs(mount_root, rel)):
            continue
        if not _folder_matches_hint(posixpath.basename(rel), *hints):
            continue
        disk_files = _list_dir_files(mount_root, rel)
        if _files_match_specs(disk_files, specs) or _has_subdir_match(mount_root, rel, specs):
            return rel, disk_files

    # Fuzzy pass: only direct children of the qBittorrent root or of the
    # category, and only on a strong name match.
    for scope in scopes:
        for name in _list_subdirs(mount_root, scope):
            if not _folder_matches_hint(name, *hints):
                continue
            rel = posixpath.join(scope, name) if scope else name
            if rel in candidates:
                continue
            candidates.append(rel)
            disk_files = _list_dir_files(mount_root, rel)
            if _files_match_specs(disk_files, specs) or _has_subdir_match(mount_root, rel, specs):
                return rel, disk_files

    return None


def _skipped_entry(torrent: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "hash": str(torrent.get("hash") or "").lower(),
        "name": str(torrent.get("name") or "Torrent"),
        "category": str(torrent.get("category") or ""),
        "reason": reason,
    }


def _scan_index(mount_root: str, qbit_root: str) -> dict[str, Any]:
    """Return the cached scan, serializing misses across worker threads."""
    with _SCAN_LOCK:
        return _scan_index_locked(mount_root, qbit_root)


def _scan_index_locked(mount_root: str, qbit_root: str) -> dict[str, Any]:
    """Index every file under the qBittorrent download root on the mount.

    Returns ``{"anchor_prefix": str, "files": {basename.casefold(): {dir_rel: size}},
    "sizes": {size: [dir_rel, ...]}, "dir_basenames": {basename.casefold(): [dir_rel, ...]}}``
    where ``dir_rel`` paths are relative to ``mount_root``.
    """
    mount_root = os.path.realpath(mount_root)
    qbit_root = _normalize_path(qbit_root)
    cache_key = (mount_root, qbit_root)
    now = time.monotonic()
    cache = _SCAN_CACHE
    if (
        cache["data"] is not None
        and cache.get("key") == cache_key
        and now - cache["ts"] < _SCAN_CACHE_TTL
    ):
        return cache["data"]

    anchor_prefix = _resolve_anchor_prefix(mount_root, qbit_root)
    scan_root = _mount_rel_to_abs(mount_root, anchor_prefix)

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
        "dir_keys": {key.casefold() for key in dir_files},
        "path_keys": {
            posixpath.join(directory, filename).casefold()
            for directory, entries in dir_files.items()
            for filename in entries
        },
    }
    cache["data"] = data
    cache["ts"] = time.monotonic()
    cache["key"] = cache_key
    return data


async def _scan_index_async(mount_root: str, qbit_root: str) -> dict[str, Any]:
    """Run the potentially slow FUSE traversal off the event loop."""
    return await asyncio.to_thread(_scan_index, mount_root, qbit_root)


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
        best_candidate = max(candidates.items(), key=_score)[0]
        # Games often keep their executables in a nested data/bin folder.  The
        # contentPath hint is the real torrent root; prefer it over the deepest
        # folder that merely contains the matching files.
        if hint:
            for hint_dir in index["dir_basenames"].get(hint, []):
                if best_candidate == hint_dir or best_candidate.startswith(hint_dir + "/"):
                    return hint_dir
        return best_candidate

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


def _common_root_name(paths: list[str]) -> str:
    """Return the common first folder of a qBittorrent file list, if any."""
    parts = [str(path or "").replace("\\", "/").strip("/").split("/", 1) for path in paths]
    if paths and all(len(item) == 2 for item in parts):
        candidate = parts[0][0]
        if candidate and all(item[0] == candidate for item in parts):
            return candidate
    return ""


async def _torrent_file_manifest(qbit: Any, torrent_hash: str) -> tuple[list[tuple[str, int]], str]:
    """Return (basename+size specs, torrent root folder name)."""
    try:
        payload = await qbit.files(torrent_hash)
    except QbitError:
        return [], ""
    specs: list[tuple[str, int]] = []
    paths: list[str] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").replace("\\", "/").strip("/")
        basename = _file_basename(name)
        if not basename:
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        specs.append((basename, size))
        paths.append(name)
    return specs, _common_root_name(paths)


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


def _category_rel_for(
    torrent: dict[str, Any],
    specs: list[tuple[str, int]],
    qbit_root: str,
    anchor_prefix: str,
    folder_hint: str,
) -> str:
    """Return the mount-relative category folder for a torrent.

    Priority: the folder qBittorrent already points at (when it is a real
    library category), then an explicit non-automation category, then the
    canonical category derived from the media kind.
    """
    current = _normalize_path(str(torrent.get("savePath") or torrent.get("save_path") or ""))
    torrent_name = str(torrent.get("name") or "")
    save_category = ""
    if qbit_root and current and current != qbit_root and _is_shallow_save_path(qbit_root, current):
        candidate = _safe_folder_name(posixpath.basename(current))
        # A save path pointing directly at the torrent's own folder is not a
        # category: it must not turn that folder into a container.
        if candidate and not _folder_matches_hint(candidate, folder_hint, torrent_name):
            save_category = candidate
    qbit_category = _safe_folder_name(str(torrent.get("category") or ""))
    if qbit_category and _folder_matches_hint(qbit_category, folder_hint, torrent_name):
        qbit_category = ""

    for name in (save_category, qbit_category):
        if name and name.casefold() not in _GENERIC_CATEGORIES:
            return posixpath.join(anchor_prefix, name) if anchor_prefix else name

    classification = _classify_torrent(torrent, [name for name, _size in specs])
    if classification:
        canonical = _CATEGORY_BY_KIND.get(classification[0], "")
        if canonical:
            return posixpath.join(anchor_prefix, canonical) if anchor_prefix else canonical
    return ""


def _category_names_for(
    torrent: dict[str, Any],
    current: str,
    category_rel: str,
    folder_hint: str,
) -> set[str]:
    names = set(_KNOWN_LIBRARY_CATEGORIES)
    torrent_name = str(torrent.get("name") or "")
    candidates = (
        str(torrent.get("category") or ""),
        posixpath.basename(current) if current else "",
        posixpath.basename(category_rel) if category_rel else "",
    )
    for value in candidates:
        cleaned = _safe_folder_name(value)
        if not cleaned:
            continue
        if cleaned.casefold() not in _KNOWN_LIBRARY_CATEGORIES and _folder_matches_hint(cleaned, folder_hint, torrent_name):
            continue
        names.add(cleaned.casefold())
    return names


def _is_container(
    located: str,
    anchor_prefix: str,
    torrent_name: str,
    folder_hint: str,
    category_names: set[str],
) -> bool:
    """True when a folder is a shared container, not the torrent's own root."""
    located = located.rstrip("/")
    anchor = anchor_prefix.rstrip("/")
    if located in {"", ".", anchor}:
        return True
    parent = posixpath.dirname(located)
    if parent != anchor:
        return False
    basename = posixpath.basename(located)
    if basename.casefold() in category_names:
        return True
    return not _folder_matches_hint(basename, folder_hint, torrent_name)


def _match_disk_files(
    disk_files: dict[str, int],
    specs: list[tuple[str, int]],
) -> list[tuple[str, str]]:
    """Match torrent files to on-disk names, preferring exact names over sizes."""
    used: set[str] = set()
    matches: list[tuple[str, str]] = []
    by_name = {name.casefold(): name for name in disk_files}
    for spec_name, spec_size in specs:
        target = posixpath.basename(spec_name)
        matched: str | None = by_name.get(target.casefold())
        if matched is not None and matched in used:
            matched = None
        if matched is None and spec_size > 0:
            for disk_name, disk_size in disk_files.items():
                if disk_name in used:
                    continue
                if disk_size == spec_size:
                    matched = disk_name
                    break
        if matched is None:
            for disk_name in disk_files:
                if disk_name in used:
                    continue
                if _name_tokens(disk_name) == _name_tokens(target):
                    matched = disk_name
                    break
        if matched is None:
            continue
        used.add(matched)
        matches.append((target, matched))
    return matches


def _file_renames(disk_files: dict[str, int], located: str, specs: list[tuple[str, int]]) -> list[dict[str, str]]:
    ops: list[dict[str, str]] = []
    for target, matched in _match_disk_files(disk_files, specs):
        if matched != target:
            ops.append({"op": "rename", "path": located, "old_name": matched, "new_name": target})
    return ops


def _container_ops(
    disk_files: dict[str, int],
    located: str,
    torrent_name: str,
    specs: list[tuple[str, int]],
    destination: str,
) -> list[dict[str, str]]:
    """Build operations that move the files of a shared container.

    ``destination`` is the folder that must hold the torrent content.  A
    multi-file torrent gets a subfolder named after the torrent; a single-file
    torrent is renamed/moved directly.
    """
    matches = _match_disk_files(disk_files, specs)
    single_file = len(specs) <= 1
    ops: list[dict[str, str]] = []
    if single_file:
        if matches:
            target_name, matched = matches[0]
            if destination == located:
                if matched != target_name:
                    ops.append({"op": "rename", "path": located, "old_name": matched, "new_name": target_name})
            else:
                ops.append({
                    "op": "move",
                    "path": located,
                    "old_name": matched,
                    "new_name": target_name if matched != target_name else "",
                    "dest": destination,
                })
        return ops

    ops.append({"op": "mkdir", "path": destination, "name": torrent_name})
    subfolder = posixpath.join(destination, torrent_name)
    for target_name, matched in matches:
        ops.append({
            "op": "move",
            "path": located,
            "old_name": matched,
            "new_name": target_name if matched != target_name else "",
            "dest": subfolder,
        })
    return ops


def _mkdir_category_ops(category_rel: str) -> list[dict[str, str]]:
    if not category_rel:
        return []
    parent = posixpath.dirname(category_rel)
    name = posixpath.basename(category_rel)
    if not name:
        return []
    return [{"op": "mkdir", "path": parent, "name": name}]


def _destination_exists(
    mount_root: str,
    rel: str,
    index: dict[str, Any] | None,
) -> bool:
    if index is not None:
        keys = index.get("dir_keys")
        if keys is None:
            keys = {key.casefold() for key in index.get("dir_files", {})}
        path_keys = index.get("path_keys") or set()
        target = rel.casefold()
        return target in keys or target in path_keys
    return os.path.exists(_mount_rel_to_abs(mount_root, rel))


def _add_group(
    groups: dict[tuple[str, str], dict[str, Any]],
    entry: dict[str, Any],
) -> None:
    key = (entry["targetPath"], entry["layout"])
    group = groups.setdefault(
        key,
        {"location": entry["targetPath"], "layout": entry["layout"], "hashes": [], "names": [], "entries": []},
    )
    group["hashes"].append(entry["hash"])
    group["names"].append(entry["name"])
    group["entries"].append(entry)


async def build_relink_plan(
    qbit: Any,
    *,
    hashes: list[str] | None = None,
    mount_root: str | None = None,
) -> dict[str, Any]:
    """Build the in-place and category repair plans without mutating anything."""
    mount_root = mount_root or MEDIA_MOUNT_PATH
    torrents = await qbit.torrents()
    selected_hashes = {str(item).lower() for item in (hashes or [])} if hashes else None
    targeted = selected_hashes is not None
    qbit_root = _normalize_path(QBIT_SAVE_PATH)
    started_at = time.perf_counter()

    index: dict[str, Any] | None = None
    anchor_prefix = ""
    if qbit_root:
        anchor_prefix = await asyncio.to_thread(_resolve_anchor_prefix, mount_root, qbit_root)

    async def ensure_index() -> dict[str, Any]:
        nonlocal index
        if index is None:
            index = await _scan_index_async(mount_root, qbit_root)
            logger.info(
                "relink: fallback scan mount=%s anchor=%s fichiers=%d",
                mount_root,
                index["anchor_prefix"] or "(racine du mount)",
                len(index["files"]),
            )
        return index

    groups_in_place: dict[tuple[str, str], dict[str, Any]] = {}
    groups_category: dict[tuple[str, str], dict[str, Any]] = {}
    recheck_only_in_place: list[dict[str, Any]] = []
    recheck_only_category: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    included = 0
    category_fallbacks = 0

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

        specs, torrent_root = await _torrent_file_manifest(qbit, torrent_hash)
        if not specs:
            logger.info("relink: %s (%s) ignoré — aucun fichier connu", torrent_name, torrent_hash[:8])
            skipped.append(_skipped_entry(torrent, "Aucun fichier connu"))
            continue

        if not qbit_root:
            logger.info("relink: %s (%s) ignoré — racine qBittorrent non configurée", torrent_name, torrent_hash[:8])
            skipped.append(_skipped_entry(torrent, "Sans racine qBittorrent configurée"))
            continue

        folder_hint = _file_basename(torrent.get("contentPath") or torrent.get("name") or "")
        content_name = _file_basename(torrent_root or torrent_name) or "Torrent"
        category_rel = _category_rel_for(torrent, specs, qbit_root, anchor_prefix, folder_hint)

        located: str | None = None
        disk_files: dict[str, int] = {}
        if index is None:
            found = await asyncio.to_thread(
                _locate_folder_fast,
                mount_root,
                anchor_prefix,
                category_rel,
                content_name,
                folder_hint,
                specs,
            )
            if found is not None:
                located, disk_files = found
        if not located:
            scan = await ensure_index()
            located = _locate_folder(scan, specs, folder_hint)
            if located:
                disk_files = scan["dir_files"].get(located, {})

        if not located:
            logger.info("relink: %s (%s) ignoré — contenu non localisé (savePath=%s)", torrent_name, torrent_hash[:8], current)
            skipped.append(_skipped_entry(torrent, "Contenu organisé non localisé"))
            continue

        located_clean = located.rstrip("/")
        category_names = _category_names_for(torrent, current, category_rel, folder_hint)
        is_container = _is_container(located_clean, anchor_prefix, content_name, folder_hint, category_names)

        if is_container:
            in_target_rel = located_clean
            in_layout = _LAYOUT_NO_SUBFOLDER if len(specs) <= 1 else _LAYOUT_SUBFOLDER
            in_ops = _container_ops(disk_files, located_clean, content_name, specs, located_clean)
        else:
            in_target_rel = posixpath.dirname(located_clean) or located_clean
            in_layout = _LAYOUT_SUBFOLDER
            in_ops = _file_renames(disk_files, located_clean, specs)
            if posixpath.basename(located_clean) != content_name:
                in_ops.append({
                    "op": "rename",
                    "path": in_target_rel,
                    "old_name": posixpath.basename(located_clean),
                    "new_name": content_name,
                })

        in_target = _anchor_target(qbit_root, anchor_prefix, in_target_rel)
        if not in_target:
            logger.info("relink: %s (%s) ignoré — cible non résolue (locate=%s)", torrent_name, torrent_hash[:8], located)
            skipped.append(_skipped_entry(torrent, "Chemin cible non résolu"))
            continue

        cat_target_rel = in_target_rel
        cat_layout = in_layout
        cat_ops = list(in_ops)
        cat_applied = False
        cat_reason = "Catégorie indisponible"

        if category_rel:
            if _path_within(located_clean, category_rel):
                cat_applied = True
                cat_reason = ""
            elif is_container:
                if located_clean in {"", ".", anchor_prefix.rstrip("/")}:
                    if len(specs) > 1:
                        dest_folder = posixpath.join(category_rel, content_name)
                        if _destination_exists(mount_root, dest_folder, index):
                            cat_reason = "Collision de destination"
                        else:
                            cat_target_rel = category_rel
                            cat_layout = _LAYOUT_SUBFOLDER
                            cat_ops = _mkdir_category_ops(category_rel) + _container_ops(
                                disk_files, located_clean, content_name, specs, category_rel
                            )
                            cat_applied = True
                            cat_reason = ""
                    else:
                        cat_target_rel = category_rel
                        cat_layout = _LAYOUT_NO_SUBFOLDER
                        cat_ops = _mkdir_category_ops(category_rel) + _container_ops(
                            disk_files, located_clean, content_name, specs, category_rel
                        )
                        cat_applied = True
                        cat_reason = ""
                else:
                    cat_reason = "Catégorie ambiguë"
            else:
                dest_folder = posixpath.join(category_rel, content_name)
                if _destination_exists(mount_root, dest_folder, index):
                    cat_reason = "Collision de destination"
                else:
                    cat_target_rel = category_rel
                    cat_layout = _LAYOUT_SUBFOLDER
                    cat_ops = _mkdir_category_ops(category_rel) + _file_renames(disk_files, located_clean, specs)
                    parent_rel = posixpath.dirname(located_clean)
                    basename = posixpath.basename(located_clean)
                    cat_ops.append({
                        "op": "move",
                        "path": parent_rel,
                        "old_name": basename,
                        "new_name": content_name if basename != content_name else "",
                        "dest": category_rel,
                    })
                    cat_applied = True
                    cat_reason = ""

        cat_target = _anchor_target(qbit_root, anchor_prefix, cat_target_rel)
        if not cat_target:
            cat_target = in_target
            cat_target_rel = in_target_rel
            cat_layout = in_layout
            cat_ops = list(in_ops)
            cat_applied = False
            cat_reason = "Chemin cible non résolu"

        included += 1
        if not cat_applied:
            category_fallbacks += 1

        common = {
            "hash": torrent_hash,
            "name": torrent_name,
            "category": str(torrent.get("category") or ""),
            "currentPath": current,
            "located": located_clean,
            "categoryTargetPath": cat_target,
            "categoryApplied": cat_applied,
            "categoryReason": cat_reason,
        }
        in_entry = {
            **common,
            "targetPath": in_target,
            "layout": in_layout,
            "renames": in_ops,
        }
        cat_entry = {
            **common,
            "targetPath": cat_target,
            "layout": cat_layout,
            "renames": cat_ops,
        }

        if not in_ops and current == in_target:
            logger.debug("relink: %s (%s) déjà au bon endroit (%s)", torrent_name, torrent_hash[:8], in_target)
            recheck_only_in_place.append(in_entry)
        else:
            _add_group(groups_in_place, in_entry)

        if not cat_ops and current == cat_target:
            recheck_only_category.append(cat_entry)
        else:
            _add_group(groups_category, cat_entry)

        logger.info(
            "relink: %s (%s) in-place %s → %s (%d op), catégorie %s → %s (%d op, appliqué=%s)",
            torrent_name,
            torrent_hash[:8],
            current,
            in_target,
            len(in_ops),
            current,
            cat_target,
            len(cat_ops),
            cat_applied,
        )

    relink_count = sum(len(group["hashes"]) for group in groups_in_place.values())
    category_count = sum(len(group["hashes"]) for group in groups_category.values())
    logger.info(
        "relink: plan prêt en %.0f ms — %d réalignements, %d classements, %d repli(s), %d ignorés, scan=%s",
        (time.perf_counter() - started_at) * 1000,
        relink_count,
        category_count,
        category_fallbacks,
        len(skipped),
        "complet" if index is not None else "aucun (fast path)",
    )
    return {
        "relink": sorted(groups_in_place.values(), key=lambda group: group["location"]),
        "recheckOnly": recheck_only_in_place,
        "relinkCategory": sorted(groups_category.values(), key=lambda group: group["location"]),
        "recheckOnlyCategory": recheck_only_category,
        "skipped": skipped,
        "total": included,
        "relinkCount": relink_count,
        "categoryRelinkCount": category_count,
        "recheckOnlyCount": len(recheck_only_in_place),
        "categoryRecheckOnlyCount": len(recheck_only_category),
        "categoryFallbackCount": category_fallbacks,
        "skippedCount": len(skipped),
        "layout": _LAYOUT_SUBFOLDER,
        "generatedAt": now_iso(),
    }


async def apply_relink_plan(
    qbit: Any,
    plan: dict[str, Any],
    *,
    recheck: bool = False,
    mode: str = "in_place",
) -> dict[str, Any]:
    """Apply a plan: rename + pause + set location/layout.

    Files are renamed to match the torrent (folder = torrent name, files = the
    torrent content names) via the cloud-panel (which has write access to the
    mount), then qBittorrent is pointed at the target folder.  Torrents are
    left paused so the user can verify before resuming.

    ``mode="category"`` uses the category groups of the plan (content moved to
    ``Jeux``/``Films``/``Series``/``Livres``).  The default ``in_place`` mode
    only points qBittorrent at the current location.
    """
    from .cloud_panel import CloudPanelError, arrange_batch

    selected_groups = plan.get("relinkCategory" if mode == "category" else "relink", [])
    selected_recheck = plan.get("recheckOnlyCategory" if mode == "category" else "recheckOnly", [])

    all_hashes = [entry["hash"] for group in selected_groups for entry in group["entries"]]
    all_hashes.extend(entry["hash"] for entry in selected_recheck)
    all_hashes = list(dict.fromkeys(all_hashes))

    failures: list[dict[str, Any]] = []
    relinked = 0
    successful_hashes: list[str] = []
    details: list[dict[str, Any]] = []

    # qBittorrent answers 404 on hash-bearing endpoints (setLocation,
    # setContentLayout, pause) when any hash is unknown, failing the whole
    # batch. Re-sync the known torrent list so a single stale hash (torrent
    # removed between the plan build and the apply, e.g. by auto_relink) does
    # not block the valid torrents of the same group.
    current_hashes: set[str] | None = None
    try:
        current_hashes = {str(t.get("hash") or "").lower() for t in await qbit.torrents()}
    except QbitError:
        current_hashes = None

    if all_hashes:
        pause_hashes = [h for h in all_hashes if current_hashes is None or h in current_hashes]
        if pause_hashes:
            try:
                await qbit.pause_many(pause_hashes)
                logger.info("relink: %d torrent(s) mis en pause", len(pause_hashes))
            except QbitError:
                pass

    for group in selected_groups:
        hashes = list(group["hashes"])
        names_by_hash = dict(zip(hashes, group["names"]))
        if current_hashes is not None:
            known = [h for h in hashes if h in current_hashes]
            for stale_hash in (h for h in hashes if h not in current_hashes):
                failures.append(
                    {
                        "hash": stale_hash,
                        "name": names_by_hash[stale_hash],
                        "message": "Torrent inconnu côté qBittorrent.",
                    }
                )
                logger.warning(
                    "relink: %s (%s) ignoré — torrent absent côté qBittorrent",
                    names_by_hash[stale_hash],
                    stale_hash[:8],
                )
        else:
            known = list(hashes)
        entries = [entry for entry in group["entries"] if entry["hash"] in known]

        if not entries:
            details.append({"location": group["location"], "count": len(hashes), "ok": False, "error": "Aucun torrent restant"})
            continue

        rename_ok = True

        for entry in entries:
            entry_ops = entry.get("renames") or []
            if not entry_ops:
                continue
            try:
                result = await arrange_batch(entry_ops)
                failed = int(result.get("failed", 0))
                if failed:
                    for item in result.get("results", []):
                        if not item.get("success"):
                            logger.warning(
                                "relink: op refusée %s → %s (%s%s%s)",
                                item.get("op"),
                                item.get("path"),
                                item.get("old_name") or "",
                                " → " + item.get("new_name") if item.get("new_name") else "",
                                " (dest " + item.get("dest") + ")" if item.get("dest") else "",
                            )
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

        group_layout = group.get("layout", _LAYOUT_SUBFOLDER)

        ok_hashes: list[str] = []
        failure_error: str | None = None
        try:
            await qbit.set_location_many(known, group["location"])
            ok_hashes = list(known)
        except QbitError as exc:
            if exc.code == "qbit_action_unavailable" and len(known) > 1:
                # One or more hashes vanished between the reconcile and the
                # call: retry one by one so the valid torrents still relocate.
                for torrent_hash in known:
                    try:
                        await qbit.set_location_many([torrent_hash], group["location"])
                        ok_hashes.append(torrent_hash)
                    except QbitError as per_exc:
                        per_msg = (
                            "Torrent inconnu côté qBittorrent."
                            if per_exc.code == "qbit_action_unavailable"
                            else per_exc.public_message
                        )
                        failures.append({"hash": torrent_hash, "name": names_by_hash[torrent_hash], "message": per_msg})
                        logger.warning(
                            "relink: échec setLocation %s → %s : %s",
                            torrent_hash[:8],
                            group["location"],
                            per_msg,
                        )
            else:
                failure_error = exc.public_message
                for torrent_hash in known:
                    failures.append({"hash": torrent_hash, "name": names_by_hash[torrent_hash], "message": exc.public_message})
                logger.warning("relink: échec setLocation → %s : %s", group["location"], exc.public_message)

        if not ok_hashes:
            details.append(
                {
                    "location": group["location"],
                    "count": len(known),
                    "ok": False,
                    "error": failure_error or "Aucun torrent repositionné",
                }
            )
            continue

        try:
            await qbit.set_content_layout_many(ok_hashes, group_layout)
        except QbitError as exc:
            logger.warning("relink: échec setContentLayout → %s : %s", group["location"], exc.public_message)

        relinked += len(ok_hashes)
        successful_hashes.extend(ok_hashes)
        details.append({"location": group["location"], "layout": group_layout, "count": len(ok_hashes), "ok": True})
        logger.info("relink: %d torrent(s) → %s (layout %s)", len(ok_hashes), group["location"], group_layout)

    failed_rechecks = 0
    recheck_candidates = successful_hashes + [entry["hash"] for entry in selected_recheck]
    recheck_candidates = list(dict.fromkeys(recheck_candidates))
    if recheck and recheck_candidates:
        recheck_hashes = [h for h in recheck_candidates if current_hashes is None or h in current_hashes]
        if recheck_hashes:
            try:
                await qbit.recheck_many(recheck_hashes)
                logger.info("relink: recheck demandé sur %d torrent(s)", len(recheck_hashes))
            except QbitError:
                failed_rechecks = len(recheck_hashes)
                logger.warning("relink: recheck impossible sur %d torrent(s)", len(recheck_hashes))
        else:
            recheck_hashes = []
    else:
        recheck_hashes = []

    return {
        "relinked": relinked,
        "paused": len(all_hashes),
        "rechecked": len(recheck_hashes),
        "recheckFailed": failed_rechecks,
        "failed": len(failures),
        "skipped": plan.get("skippedCount", 0),
        "mode": mode,
        "details": details,
        "failures": failures,
    }


async def relink_missing(
    qbit: Any,
    *,
    hashes: list[str] | None = None,
    preview: bool = False,
    recheck: bool = False,
    mode: str = "in_place",
) -> dict[str, Any]:
    """Build the plan and optionally apply it to the affected torrents."""
    if mode not in REPAIR_MODES:
        raise ValueError("Mode de réparation invalide.")
    plan = await build_relink_plan(qbit, hashes=hashes)
    selected_groups = plan.get("relinkCategory" if mode == "category" else "relink", [])
    selected_recheck = plan.get("recheckOnlyCategory" if mode == "category" else "recheckOnly", [])
    if preview or (not selected_groups and not (recheck and selected_recheck)):
        return {"plan": plan, "result": None}
    result = await apply_relink_plan(qbit, plan, recheck=recheck, mode=mode)
    return {"plan": plan, "result": result}


async def relink_status(qbit: Any) -> dict[str, int]:
    """Return the cheap relink candidate count without scanning the mount."""
    torrents = await qbit.torrents()
    qbit_root = _normalize_path(QBIT_SAVE_PATH)
    count = 0
    for torrent in torrents:
        state = str(torrent.get("state") or "")
        if state in MISSING_STATES or state in PAUSED_DL_STATES:
            count += 1
            continue
        current = _normalize_path(str(torrent.get("savePath") or ""))
        if _is_shallow_save_path(qbit_root, current):
            count += 1
    return {"count": count}
