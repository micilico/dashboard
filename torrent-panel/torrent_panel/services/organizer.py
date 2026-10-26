"""Safe media-library organization driven by qBittorrent.

Unlike the legacy cloud-panel organizer, this workflow never moves a file
behind qBittorrent's back.  qBittorrent renames every owned path itself, then
moves the torrent to the Films or Series root and verifies it before any
previously-active torrent may resume.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import posixpath
import re
import time
import unicodedata
from urllib.parse import unquote
from pathlib import Path
from typing import Any, Callable

from ..qbittorrent import QbitError

logger = logging.getLogger("torrent_panel.organizer")

_SEASON_RE = re.compile(
    r"(?i)(?:^|[\s._\-/])(?:S(?:eason)?|Saison)[\s._-]*(\d{1,2})(?=$|[\s._\-/E])"
)
_EPISODE_RE = re.compile(r"(?i)(?:^|[\s._\-/])S(\d{1,2})E\d{1,3}\b")
_ALT_EPISODE_RE = re.compile(r"(?i)(?:^|[\s._\-/])(\d{1,2})x\d{1,3}\b")
_SPECIAL_RE = re.compile(r"(?i)(?:^|[\s._\-/])(?:specials?|sp[eé]cial(?:e|es)?)\b")
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_TITLE_TOKEN_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
_RELEASE_NOISE_TOKENS = frozenset({
    "2160p", "1440p", "1080p", "1080i", "720p", "576p", "480p",
    "4k", "8k", "uhd", "bluray", "blu-ray", "bdrip", "brrip", "remux",
    "webrip", "web-dl", "webdl", "web", "hdtv", "dvdrip", "dvd",
    "x264", "x265", "h264", "h265", "hevc", "avc", "av1", "xvid", "divx",
    "ac3", "eac3", "dts", "dts-hd", "truehd", "atmos", "aac", "flac",
    "hdr", "hdr10", "dolby", "vision", "sdr", "10bit", "8bit",
    "multi", "multi-vff", "vf", "vff", "vfq", "vf2", "truefrench",
    "vostfr", "vosta", "sub", "subs",
    "proper", "repack", "rerip",
})
_LANGUAGE_NOISE_TOKENS = frozenset({"fr", "en", "eng", "fra", "fre"})
_VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".webm"}
_BOOK_EXTENSIONS = {
    ".epub", ".kepub", ".pdf", ".mobi", ".azw", ".azw3", ".kfx", ".lit", ".fb2", ".djvu",
    ".cbz", ".cbr", ".cbt", ".cb7", ".m4b",
}
_GAME_ROM_EXTENSIONS = {
    ".3ds", ".cia", ".gba", ".gb", ".gbc", ".gcm", ".n64", ".nds", ".nes",
    ".nsp", ".rvz", ".sfc", ".snes", ".wad", ".wbfs", ".wia", ".xci", ".z64",
}
_GAME_SIGNATURE_EXTENSIONS = {
    ".ucas", ".utoc", ".unity3d", ".vpk", ".xp3",
}
_GAME_MANIFEST_NAMES = {
    "game.json", "steam_appid.txt", "steam_api.dll", "steam_api64.dll", "unityplayer.dll",
}
_DANGEROUS_EXTENSIONS = {".rar", ".zip", ".7z", ".part", ".exe", ".msi", ".bat", ".cmd", ".scr", ".com"}
_BOOK_IMPLICIT_BLOCKED_EXTENSIONS = _DANGEROUS_EXTENSIONS | {".dll", ".dat"}
_ACTIVE_SEED_STATES = {"uploading", "stalledUP", "forcedUP", "queuedUP", "checkingUP"}
_COMPLETE_STATES = _ACTIVE_SEED_STATES | {"pausedUP", "stoppedUP"}
_CHECKING_STATES = {"checkingUP", "checkingDL", "queuedForChecking", "checkingResumeData"}
_PAUSED_STATES = {"pausedUP", "pausedDL", "stoppedUP", "stoppedDL", "missingFiles", "error"}
_CATEGORY_BY_KIND = {"film": "Films", "series": "Series", "game": "Jeux", "book": "Livres"}
_KIND_BY_CATEGORY = {category.casefold(): kind for kind, category in _CATEGORY_BY_KIND.items()}
_CATEGORY_NAMES = set(_CATEGORY_BY_KIND.values())
_GAME_RELEASE_RE = re.compile(
    r"(?i)(?:^|[\s._-])(?:gog|fitgirl|dodi|elamigos|codex|plaza|rune|tenoke|empress|reloaded|skidrow|goldberg)(?:$|[\s._-])"
    r"|(?:^|[\s._-])(?:cusa|ppsa|npub|npeb)\d{4,}"
)


def _path_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "").replace("\\", "/"))
    return posixpath.normpath(normalized).casefold()


def _inventory_generation(available: bool, files: list[dict[str, Any]]) -> str:
    """Stable fingerprint for cache and preview/apply consistency checks."""
    payload = {
        "available": bool(available),
        "files": sorted(
            [str(item.get("path") or ""), int(item.get("size") or 0)]
            for item in files
        ),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class LibraryInventory:
    """One bounded, reusable view of the mounted qBittorrent library."""

    def __init__(
        self,
        mount_root: str,
        qbit_root: str,
        files: list[dict[str, Any]],
        generation: str,
        *,
        available: bool = True,
    ) -> None:
        self.mount_root = mount_root
        self.qbit_root = qbit_root
        self.files = files
        self.generation = generation
        # An absent or unreadable mount is not the same as a confirmed empty
        # library.  Missing-file checks must not reject every torrent merely
        # because the rclone mount is temporarily unavailable.
        self.available = available
        self.path_keys = {_path_key(str(item.get("path") or "")) for item in files}
        self.signatures = {
            (str(item.get("normalizedName") or item["name"]).casefold(), int(item.get("size") or 0))
            for item in files
        }
        self.name_counts: dict[str, int] = {}
        for item in files:
            name = str(item.get("normalizedName") or item["name"]).casefold()
            self.name_counts[name] = self.name_counts.get(name, 0) + 1

    @property
    def built_at(self) -> float:
        return float(getattr(self, "_built_at", 0.0))


def build_library_inventory(mount_root: str, qbit_root: str, *, max_entries: int = 100_000) -> LibraryInventory:
    """List the library once, collecting cheap metadata only.

    Deliberately does not open file contents.  The relative paths are safe to
    expose to the rest of the backend and never leave the backend process.
    """
    mount = os.path.realpath(mount_root or "")
    if not mount_root or not os.path.isdir(mount):
        return LibraryInventory(mount, qbit_root, [], _inventory_generation(False, []), available=False)
    anchor = mount
    root_name = posixpath.basename(qbit_root.rstrip("/"))
    try:
        direct = next((entry.path for entry in os.scandir(mount) if entry.is_dir() and entry.name.casefold() == root_name.casefold()), None)
        if direct:
            anchor = direct
    except OSError:
        return LibraryInventory(mount, qbit_root, [], _inventory_generation(False, []), available=False)
    files: list[dict[str, Any]] = []
    try:
        for directory, _dirs, names in os.walk(anchor):
            for filename in names:
                if len(files) >= max_entries:
                    break
                path = os.path.join(directory, filename)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                relative = os.path.relpath(path, anchor).replace(os.sep, "/")
                # Ne filtrer que sur la racine qBittorrent.  Un torrent peut
                # être resté dans un dossier de release directement sous
                # ``qbittorrent/`` (ou dans une arborescence créée par
                # l'ancien rangement).  Le considérer comme absent ici
                # empêcherait précisément sa réparation/organisation.
                category = relative.split("/", 1)[0] if "/" in relative else ""
                files.append({
                    "path": relative,
                    "name": filename,
                    "normalizedName": _file_name_key(filename),
                    "size": int(stat.st_size),
                    "extension": posixpath.splitext(filename)[1].lower(),
                    "parent": posixpath.dirname(relative),
                    "category": category,
                })
            if len(files) >= max_entries:
                break
    except OSError:
        return LibraryInventory(mount, qbit_root, files, _inventory_generation(False, files), available=False)
    inventory = LibraryInventory(mount, qbit_root, files, _inventory_generation(True, files))
    inventory._built_at = time.monotonic()
    return inventory


class LibraryInventoryCache:
    def __init__(self, ttl_seconds: float = 180.0) -> None:
        self.ttl_seconds = max(30.0, ttl_seconds)
        self._inventory: LibraryInventory | None = None
        self._lock = asyncio.Lock()
        self._epoch = 0

    async def get(self, mount_root: str, qbit_root: str, *, force: bool = False) -> LibraryInventory:
        mount_key = os.path.realpath(mount_root or "")
        qbit_key = _normalize_qbit_root(qbit_root)
        while True:
            async with self._lock:
                current = self._inventory
                if (
                    not force
                    and current
                    and current.mount_root == mount_key
                    and _normalize_qbit_root(current.qbit_root) == qbit_key
                    and time.monotonic() - current.built_at < self.ttl_seconds
                ):
                    return current
                epoch = self._epoch
                inventory = await asyncio.to_thread(build_library_inventory, mount_root, qbit_root)
                # invalidate() can run while the blocking mount scan is in
                # progress. Never publish or return a snapshot from the old
                # epoch; rebuild from the post-invalidation state instead.
                if epoch != self._epoch:
                    continue
                self._inventory = inventory
                return inventory

    def invalidate(self) -> None:
        self._epoch += 1
        self._inventory = None


def _duplicate_key(directory: str, files: list[str]) -> tuple[str, str] | None:
    """Return a stable identity for one Jellyfin-like media directory."""
    value = posixpath.basename(directory)
    season = _season_number(value) or next((_season_number(name) for name in files), None)
    if season is not None:
        title = _series_title(value) or next((_series_title(name) for name in files), None)
        return ("series", _clean_title(title)) if title else None
    movie = _movie_identity(value) or next((_movie_identity(name) for name in files), None)
    return ("film", f"{movie[0]}|{movie[1]}") if movie else None


def _file_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _duplicate_file_decision(left: dict[str, Any], right: dict[str, Any]) -> str:
    """Compare two files progressively and return an explicit decision."""
    if left["name"].casefold() == right["name"].casefold() and left["size"] != right["size"]:
        return "conflict"
    if left["size"] != right["size"]:
        return "keep_both"
    if left.get("digest") and right.get("digest") and left["digest"] == right["digest"]:
        return "reuse"
    return "conflict" if left["name"].casefold() == right["name"].casefold() else "keep_both"


def detect_duplicate_groups(mount_root: str, qbit_root: str) -> list[dict[str, Any]]:
    """Build a read-only duplicate plan from the mounted library.

    Only directories below Films and Series are considered. Hashes are computed
    for same-name/same-size candidates, keeping the usual scan cheap.
    """
    mount = os.path.realpath(mount_root or "")
    if not os.path.isdir(mount):
        return []
    anchor = mount
    root_name = posixpath.basename(qbit_root.rstrip("/"))
    try:
        candidate = next((entry.path for entry in os.scandir(mount) if entry.is_dir() and entry.name.casefold() == root_name.casefold()), None)
        if candidate:
            anchor = candidate
    except OSError:
        return []
    folders: list[dict[str, Any]] = []
    for category in ("Films", "Series"):
        category_path = os.path.join(anchor, category)
        if not os.path.isdir(category_path):
            continue
        try:
            directories = [entry for entry in os.scandir(category_path) if entry.is_dir()]
        except OSError:
            continue
        for entry in directories:
            files: list[dict[str, Any]] = []
            for directory, _dirs, names in os.walk(entry.path):
                for name in names:
                    path = os.path.join(directory, name)
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        continue
                    relative = os.path.relpath(path, entry.path).replace(os.sep, "/")
                    files.append({"name": relative, "size": size, "path": path})
            identity = _duplicate_key(entry.name, [item["name"] for item in files])
            if identity and files:
                folders.append({"category": category, "name": entry.name, "path": f"{category}/{entry.name}", "identity": identity, "files": files})
    groups: list[dict[str, Any]] = []
    for identity in sorted({item["identity"] for item in folders}):
        members = [item for item in folders if item["identity"] == identity]
        if len(members) < 2:
            continue
        kind, key = identity
        year = key.rsplit("|", 1)[-1] if kind == "film" else ""
        title = key.split("|", 1)[0] if kind == "film" else key
        canonical_name = f"{title} ({year})" if kind == "film" else title
        canonical_member = next((item for item in members if item["name"] == canonical_name), members[0])
        canonical_path = f"{canonical_member['category']}/{canonical_name}"
        canonical_files = {item["name"].casefold(): item for item in canonical_member["files"]}
        decisions: list[dict[str, Any]] = []
        warnings: list[str] = []
        exact_files = 0
        conflicts = 0
        recoverable = 0
        for member in members:
            if member is canonical_member:
                continue
            for item in member["files"]:
                target = canonical_files.get(item["name"].casefold())
                if target is None:
                    decision = "move"
                else:
                    try:
                        item["digest"] = _file_digest(item["path"])
                        target["digest"] = target.get("digest") or _file_digest(target["path"])
                    except OSError:
                        decision = "manual"
                    else:
                        decision = _duplicate_file_decision(item, target)
                if decision == "reuse":
                    exact_files += 1
                    recoverable += item["size"]
                elif decision == "conflict":
                    conflicts += 1
                    warnings.append(f"Conflit de contenu : {item['name']}")
                decisions.append({"sourcePath": f"{member['path']}/{item['name']}", "targetPath": f"{canonical_path}/{item['name']}", "name": item["name"], "size": item["size"], "decision": decision})
        status = "conflict" if conflicts else "ready"
        groups.append({
            "id": hashlib.sha256("|".join(sorted(item["path"] for item in members)).encode()).hexdigest()[:16],
            "kind": kind,
            "canonicalPath": canonical_path,
            "sourcePaths": [item["path"] for item in members if item is not canonical_member],
            "files": decisions,
            "status": status,
            "warnings": warnings,
            "exactFiles": exact_files,
            "complementaryFiles": sum(1 for item in decisions if item["decision"] == "move"),
            "conflicts": conflicts,
            "recoverableBytes": recoverable,
            "associatedTorrents": [],
            "proposedDecision": "manual" if conflicts else "merge",
        })
    return groups


def duplicate_cloud_operations(group: dict[str, Any], qbit_root: str) -> list[dict[str, str]]:
    """Translate approved file decisions to cloud-panel move/trash operations."""
    cloud_root = posixpath.basename(qbit_root.rstrip("/"))
    operations: list[dict[str, str]] = []
    canonical = posixpath.join(cloud_root, str(group.get("canonicalPath") or ""))
    operations.append({"op": "mkdir", "path": posixpath.dirname(canonical), "name": posixpath.basename(canonical)})
    for item in group.get("files", []):
        decision = str(item.get("decision") or "")
        source = posixpath.join(cloud_root, str(item.get("sourcePath") or ""))
        target = posixpath.join(cloud_root, str(item.get("targetPath") or ""))
        if decision == "move":
            operations.append({"op": "move", "path": posixpath.dirname(source), "old_name": posixpath.basename(source), "dest": posixpath.dirname(target), "new_name": posixpath.basename(target)})
        elif decision == "reuse":
            operations.append({"op": "delete", "path": posixpath.dirname(source), "old_name": posixpath.basename(source)})
    return operations


def verify_duplicate_group(group: dict[str, Any], mount_root: str, qbit_root: str) -> dict[str, Any]:
    """Perform strong content checks only for a group the user selected."""
    anchor = os.path.realpath(mount_root or "")
    root_name = posixpath.basename(qbit_root.rstrip("/"))
    try:
        direct = next((entry.path for entry in os.scandir(anchor) if entry.is_dir() and entry.name.casefold() == root_name.casefold()), None)
    except OSError:
        direct = None
    anchor = direct or anchor
    canonical_files: dict[str, str] = {}
    canonical = str(group.get("canonicalPath") or "")
    for item in group.get("files", []):
        if item.get("decision") not in {"verification_required", "reuse", "conflict"}:
            continue
        relative_target = str(item.get("targetPath") or "")
        target = os.path.join(anchor, relative_target)
        source = os.path.join(anchor, str(item.get("sourcePath") or ""))
        try:
            source_digest = _file_digest(source)
            target_digest = _file_digest(target)
        except OSError:
            item["decision"] = "manual"
        else:
            item["decision"] = "reuse" if source_digest == target_digest else "conflict"
        canonical_files[relative_target.casefold()] = target
    conflicts = sum(1 for item in group.get("files", []) if item.get("decision") == "conflict")
    group["status"] = "conflict" if conflicts else "ready"
    group["warnings"] = ["Conflit de contenu — aucune suppression effectuée"] if conflicts else []
    group["exactFiles"] = sum(1 for item in group.get("files", []) if item.get("decision") == "reuse")
    group["recoverableBytes"] = sum(int(item.get("size") or 0) for item in group.get("files", []) if item.get("decision") == "reuse")
    return group


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
    words = [part for part in re.split(r"[\s._()\[\]-]+", value.strip(" ._()-[]")) if part]
    return " ".join(part if part.isupper() and len(part) <= 4 else part[:1].upper() + part[1:].lower() for part in words)


def _safe_relative(value: str) -> str:
    normalized = posixpath.normpath(str(value or "").replace("\\", "/")).lstrip("/")
    if not normalized or normalized == "." or normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise ValueError("Chemin torrent invalide")
    return normalized


def _file_name_key(value: str) -> str:
    """Normalize API/cloud filenames before comparing them."""
    decoded = unquote(str(value or "").replace("\\", "/").rsplit("/", 1)[-1])
    return unicodedata.normalize("NFKC", decoded).casefold()


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


def _relative_to_qbit_root(base: str, path: str, qbit_root: str) -> str | None:
    """Resolve one qBittorrent file against its save path and library root."""
    root = posixpath.normpath(str(qbit_root or "").replace("\\", "/").rstrip("/"))
    base_path = str(base or "").replace("\\", "/").strip()
    relative_path = unquote(str(path or "").replace("\\", "/").lstrip("/"))
    if not root or not relative_path:
        return None
    if not posixpath.isabs(base_path):
        root_name = posixpath.basename(root)
        if base_path == root_name or base_path.startswith(root_name + "/"):
            base_path = posixpath.join(posixpath.dirname(root), base_path)
        else:
            base_path = posixpath.join(root, base_path)
    absolute = posixpath.normpath(posixpath.join(base_path, relative_path))
    if absolute == root:
        return ""
    prefix = root + "/"
    if not absolute.startswith(prefix):
        return None
    return absolute[len(prefix) :]


def _torrent_manifest_items(
    torrent: dict[str, Any],
    files: list[dict[str, Any]],
    qbit_root: str,
) -> list[tuple[dict[str, Any], str | None]]:
    base = str(torrent.get("savePath") or torrent.get("save_path") or "")
    result: list[tuple[dict[str, Any], str | None]] = []
    for item in files:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        result.append((item, _relative_to_qbit_root(base, str(item["name"]), qbit_root)))
    return result


def _season_number(value: str) -> int | None:
    match = _EPISODE_RE.search(value) or _ALT_EPISODE_RE.search(value) or _SEASON_RE.search(value)
    if match is None and _SPECIAL_RE.search(value):
        return 0
    return int(match.group(1)) if match else None


def _series_title(value: str) -> str | None:
    match = _EPISODE_RE.search(value) or _ALT_EPISODE_RE.search(value) or _SEASON_RE.search(value) or _SPECIAL_RE.search(value)
    if not match:
        return None
    title = _clean_title(value[: match.start()])
    return title or None


def _is_release_noise(token: str) -> bool:
    normalized = token.casefold().replace("’", "'")
    return normalized in _RELEASE_NOISE_TOKENS or normalized in _LANGUAGE_NOISE_TOKENS


def _adjacent_title(value: str, *, from_end: bool) -> str:
    """Return the title run immediately touching one side of a year."""
    tokens = list(_TITLE_TOKEN_RE.finditer(value))
    if from_end:
        tokens.reverse()
    title_tokens: list[str] = []
    started = False
    for token in tokens:
        if _is_release_noise(token.group()):
            if started:
                break
            continue
        started = True
        title_tokens.append(token.group())
    if from_end:
        title_tokens.reverse()
    return " ".join(title_tokens)


def _movie_title_is_plausible(value: str) -> bool:
    if not value:
        return False
    tokens = _TITLE_TOKEN_RE.findall(value)
    return bool(tokens) and (
        any(any(character.isalpha() for character in token) for token in tokens)
        or any(re.fullmatch(r"\d{4}", token) for token in tokens)
    )


def _movie_identity(value: str) -> tuple[str, str] | None:
    """Extract a movie year and title without depending on their order.

    Release names often interleave the year and technical tokens with the
    title. Evaluate every year occurrence and keep the longest plausible
    title immediately next to one of them. Numeric four-digit titles remain
    eligible, which covers films such as ``1917`` when a release year follows.
    """
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem, extension = posixpath.splitext(name)
    if extension.casefold() in _VIDEO_EXTENSIONS:
        name = stem
    best: tuple[tuple[int, int, int], str, str] | None = None
    for match in _YEAR_RE.finditer(name):
        title_options = (
            (_adjacent_title(name[: match.start()], from_end=True), 1),
            (_adjacent_title(name[match.end() :], from_end=False), 0),
        )
        for raw_title, before_year in title_options:
            if not _movie_title_is_plausible(raw_title):
                continue
            title_tokens = _TITLE_TOKEN_RE.findall(raw_title)
            alphabetic_tokens = sum(any(character.isalpha() for character in token) for token in title_tokens)
            score = (alphabetic_tokens, len(title_tokens), before_year)
            if best is None or score > best[0]:
                best = (score, _clean_title(raw_title), match.group(1))
    return (best[1], best[2]) if best else None


def _has_extension(paths: list[str], extensions: set[str]) -> bool:
    return any(posixpath.splitext(path)[1].lower() in extensions for path in paths)


def _has_game_signature(torrent_name: str, paths: list[str]) -> bool:
    """Require a strong game cue; a generic ISO, ZIP or EXE is never enough."""
    if _has_extension(paths, _GAME_ROM_EXTENSIONS) or _has_extension(paths, _GAME_SIGNATURE_EXTENSIONS):
        return True
    normalized_paths = {path.replace("\\", "/").casefold() for path in paths}
    if any(posixpath.basename(path) in _GAME_MANIFEST_NAMES for path in normalized_paths):
        return True
    # Some packs expose game.exe together with a data directory. An EXE by
    # itself remains insufficient, even when the torrent title contains a year.
    has_named_game_exe = any(posixpath.basename(path) == "game.exe" for path in normalized_paths)
    has_game_data = any(
        part.endswith("_data") or part in {"data", "binaries", "content"}
        for path in normalized_paths
        for part in path.split("/")[:-1]
    )
    if has_named_game_exe and has_game_data:
        return True
    # A scene/release marker is contextual and becomes sufficient only beside
    # an archive, game image or installer. A weak marker cannot override a
    # video payload: PLAZA + MKV + RAR is still a video release.
    has_release_marker = bool(_GAME_RELEASE_RE.search(torrent_name))
    has_game_payload = _has_extension(paths, _DANGEROUS_EXTENSIONS | {".iso"})
    return has_release_marker and has_game_payload and not _has_extension(paths, _VIDEO_EXTENSIONS)


def _classify_torrent(torrent: dict[str, Any], paths: list[str]) -> tuple[str, str] | None:
    """Classify books and games before applying year-based film heuristics."""
    torrent_name = str(torrent.get("name") or "").strip()
    category_kind = _KIND_BY_CATEGORY.get(str(torrent.get("category") or "").strip().casefold())
    if category_kind in {"game", "book"}:
        return category_kind, "explicit_category"

    has_video = _has_extension(paths, _VIDEO_EXTENSIONS)
    if _has_game_signature(torrent_name, paths):
        return "game", "game_signature"

    has_book_file = _has_extension(paths, _BOOK_EXTENSIONS)
    has_payload_file = _has_extension(paths, _BOOK_IMPLICIT_BLOCKED_EXTENSIONS)
    if has_book_file and not has_video and not has_payload_file:
        return "book", "file_signature"

    if category_kind in {"film", "series"}:
        return category_kind, "explicit_category"

    if not has_video:
        return None
    detection_values = [torrent_name, *(path for path in paths if posixpath.splitext(path)[1].lower() in _VIDEO_EXTENSIONS)]
    if any(_season_number(value) is not None for value in detection_values):
        return "series", "heuristic"
    if any(_movie_identity(value) for value in detection_values):
        return "film", "heuristic"
    return None


def _library_folder_name(torrent_name: str) -> str:
    """Keep a release folder name while removing a single-file suffix."""
    value = str(torrent_name or "").strip().replace("\\", "/")
    value = posixpath.basename(value).strip(" .")
    extension = posixpath.splitext(value)[1].lower()
    if extension in _BOOK_EXTENSIONS or extension in _GAME_ROM_EXTENSIONS:
        value = posixpath.splitext(value)[0].strip(" .")
    return _safe_relative(value)


def _is_complete(torrent: dict[str, Any]) -> bool:
    # qBittorrent reports a completed torrent as pausedUP/stoppedUP even when
    # its floating-point progress is 0.99999 (large game releases often end on
    # a tiny remainder).  Keep a tight tolerance so an inconsistent UP state
    # can never mask a genuinely incomplete download.
    try:
        progress = float(torrent.get("progress", 0))
    except (TypeError, ValueError):
        progress = 0.0
    if str(torrent.get("state") or "") in _COMPLETE_STATES and progress >= 0.999:
        return True
    try:
        return progress >= 1 or (
            int(torrent.get("remaining", torrent.get("amount_left", 1)) or 0) == 0
            and int(torrent.get("size", 0) or 0) > 0
        )
    except (TypeError, ValueError):
        return False


def _format_short_bytes(value: int) -> str:
    size = float(max(0, int(value or 0)))
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if size < 1024 or unit == "To":
            text = f"{size:.1f}" if unit != "o" else f"{int(size)}"
            return f"{text.replace('.', ',')} {unit}"
        size /= 1024
    return f"{int(size)} o"


def _completion_detail(torrent: dict[str, Any]) -> str:
    """Human-readable reason for refusing an incomplete torrent."""
    try:
        progress = float(torrent.get("progress", 0) or 0)
    except (TypeError, ValueError):
        progress = 0.0
    try:
        remaining = int(torrent.get("remaining", torrent.get("amount_left", 0)) or 0)
    except (TypeError, ValueError):
        remaining = 0
    parts = [f"{progress * 100:.2f}".replace(".", ",") + " %"]
    if remaining > 0:
        parts.append(f"{_format_short_bytes(remaining)} restants")
    state = str(torrent.get("state") or "")
    if state:
        parts.append(f"état {state}")
    return " · ".join(parts)


def _torrent_plan(torrent: dict[str, Any], files: list[dict[str, Any]], qbit_root: str) -> dict[str, Any] | None:
    torrent_name = str(torrent.get("name") or "").strip()
    torrent_hash = str(torrent.get("hash") or "").lower()
    paths = [_safe_relative(str(item.get("name") or "")) for item in files if isinstance(item, dict) and item.get("name")]
    if not torrent_name or not torrent_hash or not paths:
        return None

    classification = _classify_torrent(torrent, paths)
    if classification is None:
        return None
    kind, confidence = classification
    media_paths = [path for path in paths if posixpath.splitext(path)[1].lower() in _VIDEO_EXTENSIONS]
    detection_values = [torrent_name, *media_paths]
    series_value = next((value for value in detection_values if _season_number(value) is not None), None)
    movie = None if series_value else next((_movie_identity(value) for value in detection_values if _movie_identity(value)), None)
    if kind == "series":
        title = _series_title(torrent_name) or next(
            (_series_title(posixpath.basename(path)) for path in media_paths if _series_title(posixpath.basename(path))),
            None,
        ) or _series_title(series_value)
        if not title:
            return None
        label = title
    elif kind == "film":
        if not movie:
            return None
        label = f"{movie[0]} ({movie[1]})"
    else:
        label = _library_folder_name(torrent_name)
    location = posixpath.join(qbit_root, _CATEGORY_BY_KIND[kind])

    common_root = _common_root(paths)
    default_season = _season_number(torrent_name) or (_season_number(series_value) if series_value else None)
    operations: list[dict[str, str]] = []
    targets: set[str] = set()
    for old_path in paths:
        remainder = _strip_common_root(old_path, common_root)
        if kind in {"film", "game", "book"}:
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
        "layout": "Structure interne préservée" if kind in {"game", "book"} else "Chemins relatifs qBittorrent",
        "confidence": confidence,
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


def _orphan_media(mount_root: str, qbit_root: str, signatures: set[tuple[str, int]]) -> list[dict[str, Any]]:
    """Return untracked video files as preview-only orphan media entries."""
    video_extensions = _VIDEO_EXTENSIONS
    mount = os.path.realpath(mount_root)
    anchor_name = posixpath.basename(qbit_root.rstrip("/")).casefold()
    anchor = mount
    try:
        for entry in os.scandir(mount):
            if entry.is_dir() and entry.name.casefold() == anchor_name:
                anchor = entry.path
                break
    except OSError:
        return []
    result: list[dict[str, Any]] = []
    for directory, _dirs, filenames in os.walk(anchor):
        for filename in filenames:
            if posixpath.splitext(filename)[1].lower() not in video_extensions:
                continue
            path = os.path.join(directory, filename)
            try:
                signature = (filename.casefold(), os.path.getsize(path))
            except OSError:
                continue
            if signature in signatures:
                continue
            relative = os.path.relpath(path, anchor).replace(os.sep, "/")
            if relative.split("/", 1)[0].casefold() in {"jeux", "livres"}:
                continue
            result.append({
                "path": relative,
                "status": "orphan",
                "message": "Média rangé — aucun torrent associé",
                "confidence": "manual_review",
            })
            if len(result) >= 500:
                return result
    return result


def _missing_torrents(
    mount_root: str,
    qbit_root: str,
    torrents: list[dict[str, Any]],
    file_payloads: list[list[dict[str, Any]]],
) -> list[dict[str, str]]:
    """Find torrents whose media manifest has no matching file on disk."""
    mount = os.path.realpath(mount_root)
    if not os.path.isdir(mount):
        return []
    disk_signatures: set[tuple[str, int]] = set()
    for directory, _dirs, filenames in os.walk(mount):
        for filename in filenames:
            try:
                disk_signatures.add((_file_name_key(filename), os.path.getsize(os.path.join(directory, filename))))
            except OSError:
                continue
    missing: list[dict[str, str]] = []
    def _size(item: dict[str, Any]) -> int:
        try:
            return int(item.get("size") or 0)
        except (TypeError, ValueError):
            return 0
    for torrent, files in zip(torrents, file_payloads):
        media_files = [
            item for item in files
            if isinstance(item, dict) and posixpath.splitext(str(item.get("name") or ""))[1].lower() in _VIDEO_EXTENSIONS
        ]
        present = False
        for item in media_files:
            name = _file_name_key(str(item.get("name") or ""))
            size = _size(item)
            if (name, size) in disk_signatures or (size == 0 and any(filename == name for filename, _disk_size in disk_signatures)):
                present = True
                break
        if media_files and not present:
            missing.append({
                "hash": str(torrent.get("hash") or "").lower(),
                "name": str(torrent.get("name") or "Torrent"),
                "reason": "Fichiers média introuvables sur le stockage — aucune mutation effectuée",
            })
    return missing


def _missing_from_inventory(
    torrents: list[dict[str, Any]],
    file_payloads: list[list[dict[str, Any]]],
    inventory: LibraryInventory,
) -> list[dict[str, str]]:
    """Cheap missing-file check using the already-built inventory."""
    if not inventory.available:
        return []
    missing: list[dict[str, str]] = []
    for torrent, files in zip(torrents, file_payloads):
        manifest_items = _torrent_manifest_items(torrent, files, inventory.qbit_root)
        paths = [str(item.get("name") or "") for item, _physical in manifest_items]
        classification = _classify_torrent(torrent, paths)
        kind = classification[0] if classification else None
        expected = (
            manifest_items
            if kind in {"game", "book"}
            else [entry for entry in manifest_items if posixpath.splitext(str(entry[0].get("name") or ""))[1].lower() in _VIDEO_EXTENSIONS]
        )

        def present_in_inventory(
            item: dict[str, Any],
            physical_path: str | None,
            *,
            allow_shared_fallback: bool,
        ) -> bool:
            if physical_path is not None:
                if _path_key(physical_path) in inventory.path_keys:
                    return True
                if not allow_shared_fallback:
                    return False
            name = _file_name_key(str(item.get("name") or ""))
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            return (name, size) in inventory.signatures or inventory.name_counts.get(name, 0) == 1

        if kind in {"game", "book"}:
            present = bool(expected) and all(
                present_in_inventory(item, physical_path, allow_shared_fallback=True)
                for item, physical_path in expected
            )
            reason = "Fichiers du manifeste introuvables sur le stockage — aucune mutation effectuée"
        else:
            present = any(
                present_in_inventory(item, physical_path, allow_shared_fallback=True)
                for item, physical_path in expected
            )
            reason = "Fichiers média introuvables sur le stockage — aucune mutation effectuée"
        if expected and not present:
            missing.append({"hash": str(torrent.get("hash") or "").lower(), "name": str(torrent.get("name") or "Torrent"), "reason": reason})
    return missing


def _orphan_from_inventory(inventory: LibraryInventory, signatures: set[tuple[str, int]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    known_names = {name for name, _size in signatures}
    for item in inventory.files:
        if item["extension"] not in _VIDEO_EXTENSIONS or (item["normalizedName"], item["size"]) in signatures or (item["normalizedName"] in known_names and inventory.name_counts.get(item["normalizedName"], 0) == 1):
            continue
        if item["path"].split("/", 1)[0].casefold() in {"jeux", "livres"}:
            continue
        result.append({"path": item["path"], "status": "orphan", "message": "Média rangé — aucun torrent associé", "confidence": "manual_review"})
        if len(result) >= 500:
            break
    return result


def _unassociated_from_inventory(inventory: LibraryInventory, signatures: set[tuple[str, int]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    known_names = {name for name, _size in signatures}
    for item in inventory.files:
        if (item["normalizedName"], item["size"]) in signatures or (item["normalizedName"] in known_names and inventory.name_counts.get(item["normalizedName"], 0) == 1):
            continue
        result.append({"name": item["path"], "reason": "Fichier présent mais non associé à qBittorrent"})
        if len(result) >= 100:
            result.append({"name": "Stockage", "reason": "Autres fichiers non associés non affichés"})
            break
    return result


def duplicate_groups_from_inventory(inventory: LibraryInventory) -> list[dict[str, Any]]:
    """Find probable video duplicate groups without reading file contents."""
    folders: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for item in inventory.files:
        parts = item["path"].split("/")
        if not parts or parts[0].casefold() not in {"films", "series"}:
            continue
        if len(parts) < 3:
            continue
        directory = "/".join(parts[:2])
        grouped.setdefault((parts[0], directory), {}).setdefault(directory, []).append({"name": "/".join(parts[2:]), "size": item["size"]})
    for (category, directory), values in grouped.items():
        files = next(iter(values.values()))
        identity = _duplicate_key(posixpath.basename(directory), [item["name"] for item in files])
        if identity:
            folders.append({"category": category, "name": posixpath.basename(directory), "path": directory, "identity": identity, "files": files})
    groups: list[dict[str, Any]] = []
    for identity in sorted({item["identity"] for item in folders}):
        members = [item for item in folders if item["identity"] == identity]
        if len(members) < 2:
            continue
        kind, key = identity
        year = key.rsplit("|", 1)[-1] if kind == "film" else ""
        title = key.split("|", 1)[0] if kind == "film" else key
        canonical_name = f"{title} ({year})" if kind == "film" else title
        canonical = next((item for item in members if item["name"] == canonical_name), members[0])
        canonical_path = f"{canonical['category']}/{canonical_name}"
        canonical_files = {item["name"].casefold(): item for item in canonical["files"]}
        decisions: list[dict[str, Any]] = []
        for member in members:
            if member is canonical:
                continue
            for item in member["files"]:
                target = canonical_files.get(item["name"].casefold())
                if target is None:
                    decision = "move"
                elif int(target["size"]) != int(item["size"]):
                    decision = "conflict"
                else:
                    decision = "verification_required"
                decisions.append({"sourcePath": f"{member['path']}/{item['name']}", "targetPath": f"{canonical_path}/{item['name']}", "name": item["name"], "size": item["size"], "decision": decision})
        conflicts = sum(1 for item in decisions if item["decision"] == "conflict")
        needs_verification = any(item["decision"] == "verification_required" for item in decisions)
        groups.append({"id": hashlib.sha256("|".join(sorted(item["path"] for item in members)).encode()).hexdigest()[:16], "kind": kind, "canonicalPath": canonical_path, "sourcePaths": [item["path"] for item in members if item is not canonical], "files": decisions, "status": "conflict" if conflicts else ("verification_required" if needs_verification else "ready"), "warnings": ["Contenu identique probable : vérification requise avant suppression"] if needs_verification else [], "exactFiles": 0, "complementaryFiles": sum(1 for item in decisions if item["decision"] == "move"), "conflicts": conflicts, "recoverableBytes": 0, "associatedTorrents": [], "proposedDecision": "manual" if conflicts or needs_verification else "merge"})
    return groups


def orphan_operations(orphan: dict[str, Any], qbit_root: str) -> list[dict[str, str]] | None:
    """Build a move-only plan for one orphan video, or return ``None`` if unclear."""
    source = _safe_relative(str(orphan.get("path") or ""))
    filename = posixpath.basename(source)
    series_season = _season_number(filename)
    if series_season is not None:
        title = _series_title(filename)
        if not title:
            return None
        target_dir = posixpath.join(qbit_root, "Series", title, f"Saison {series_season}")
    else:
        movie = _movie_identity(filename)
        if not movie:
            return None
        target_dir = posixpath.join(qbit_root, "Films", f"{movie[0]} ({movie[1]})")
    cloud_root = posixpath.basename(qbit_root.rstrip("/"))
    source_parent = posixpath.dirname(posixpath.join(cloud_root, source))
    relative_target = posixpath.relpath(target_dir, qbit_root)
    target_dir = posixpath.join(cloud_root, relative_target)
    operations: list[dict[str, str]] = []
    current = ""
    for part in relative_target.split("/"):
        next_path = posixpath.join(current, part)
        operations.append({"op": "mkdir", "path": posixpath.join(cloud_root, current), "name": part})
        current = next_path
    operations.append({"op": "move", "path": source_parent, "old_name": filename, "dest": target_dir})
    return operations


def organization_categories(plan: dict[str, Any]) -> list[str]:
    """Return only library categories touched by an organization plan."""
    categories = {
        _CATEGORY_BY_KIND[str(entry.get("kind"))]
        for entry in plan.get("entries", [])
        if str(entry.get("kind")) in _CATEGORY_BY_KIND
    }
    for orphan in plan.get("orphanMedia", []):
        category = str(orphan.get("path") or "").split("/", 1)[0]
        if category in _CATEGORY_NAMES:
            categories.add(category)
    for group in plan.get("duplicateGroups", []):
        category = str(group.get("canonicalPath") or "").split("/", 1)[0]
        if category in _CATEGORY_NAMES:
            categories.add(category)
    return sorted(categories)


async def _load_organization_snapshot(
    qbit: Any,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    torrents = list(await qbit.torrents())
    file_limit = asyncio.Semaphore(6)

    async def fetch_files(torrent_hash: str) -> list[dict[str, Any]]:
        async with file_limit:
            payload = await qbit.files(torrent_hash)
            return payload if isinstance(payload, list) else []

    file_payloads = await asyncio.gather(
        *(fetch_files(str(torrent.get("hash") or "").lower()) for torrent in torrents)
    )
    return torrents, list(file_payloads)


def _organization_snapshot_fingerprint(
    torrents: list[dict[str, Any]],
    file_payloads: list[list[dict[str, Any]]],
) -> str:
    records: list[dict[str, Any]] = []
    for torrent, files in zip(torrents, file_payloads):
        manifest: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            manifest.append({
                "name": unicodedata.normalize("NFKC", unquote(str(item["name"])).replace("\\", "/")),
                "size": size,
                "priority": int(item.get("priority") or 0),
            })
        records.append({
            "hash": str(torrent.get("hash") or "").lower(),
            "name": str(torrent.get("name") or ""),
            "category": str(torrent.get("category") or ""),
            "savePath": str(torrent.get("savePath") or torrent.get("save_path") or ""),
            "manifest": sorted(manifest, key=lambda value: (value["name"].casefold(), value["size"], value["priority"])),
        })
    payload = sorted(records, key=lambda value: (value["hash"], value["name"].casefold()))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


async def organization_source_fingerprint(qbit: Any) -> str:
    """Fingerprint every qBittorrent owner before applying a stored plan."""
    torrents, file_payloads = await _load_organization_snapshot(qbit)
    return _organization_snapshot_fingerprint(torrents, file_payloads)


async def organization_plan_revalidation_reason(
    qbit: Any,
    plan: dict[str, Any],
    selected_hashes: set[str] | None = None,
) -> str | None:
    """Return why a stored plan is stale, using one current qBittorrent snapshot."""
    torrents, file_payloads = await _load_organization_snapshot(qbit)
    expected = str(plan.get("sourceFingerprint") or "")
    if not expected or _organization_snapshot_fingerprint(torrents, file_payloads) != expected:
        return "torrents"
    selected = {
        str(value).lower()
        for value in (selected_hashes or {
            entry.get("hash") for entry in plan.get("entries", []) if isinstance(entry, dict)
        })
        if value
    }
    current_by_hash = {
        str(torrent.get("hash") or "").lower(): torrent
        for torrent in torrents
    }
    if any(hash_value not in current_by_hash or not _is_complete(current_by_hash[hash_value]) for hash_value in selected):
        return "selected"
    return None


async def build_organization_plan(
    qbit: Any,
    qbit_root: str,
    hashes: list[str] | None = None,
    *,
    mount_root: str | None = None,
    metadata_resolver: Any | None = None,
    inventory: LibraryInventory | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
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
    total_bytes = 0
    all_torrents, all_file_payloads = await _load_organization_snapshot(qbit)
    source_fingerprint = _organization_snapshot_fingerprint(all_torrents, all_file_payloads)
    selected_indexes = [
        index
        for index, torrent in enumerate(all_torrents)
        if not selected or str(torrent.get("hash") or "").lower() in selected
    ]
    torrents = [all_torrents[index] for index in selected_indexes]
    file_payloads = [all_file_payloads[index] for index in selected_indexes]
    # These functions walk the rclone mount.  Never run that blocking I/O in
    # FastAPI's event loop: otherwise even /healthz times out during a preview.
    inventory = inventory or (await asyncio.to_thread(build_library_inventory, mount_root, root) if mount_root else None)
    logger.info(
        "organize phase=inventory duration_ms=%.1f entries=%d generation=%s",
        (time.perf_counter() - started_at) * 1000,
        len(inventory.files) if inventory else 0,
        inventory.generation if inventory else "none",
    )
    missing_torrents = _missing_from_inventory(torrents, file_payloads, inventory) if inventory else []
    missing_hashes = {item["hash"] for item in missing_torrents}
    warnings.extend(missing_torrents)
    for torrent, files in zip(torrents, file_payloads):
        torrent_hash = str(torrent.get("hash") or "").lower()
        if torrent_hash in missing_hashes:
            continue
        for item in files if isinstance(files, list) else []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            total_bytes += max(0, size)
            signatures.add((_file_name_key(str(item["name"])), size))
        dangerous = [
            str(item.get("name") or "")
            for item in files
            if isinstance(item, dict)
            and posixpath.splitext(str(item.get("name") or ""))[1].lower() in _DANGEROUS_EXTENSIONS
        ]
        if dangerous:
            try:
                candidate_paths = [
                    _safe_relative(str(item.get("name") or ""))
                    for item in files
                    if isinstance(item, dict) and item.get("name")
                ]
            except ValueError:
                candidate_paths = []
            classification = _classify_torrent(torrent, candidate_paths)
            authorized_kind = classification[0] if classification else None
        else:
            authorized_kind = None
        if dangerous and authorized_kind not in {"game", "book"}:
            warnings.append({
                "hash": torrent_hash,
                "name": str(torrent.get("name") or "Torrent"),
                "reason": "Archive, fichier incomplet ou exécutable détecté — rangement automatique refusé",
                "files": ", ".join(dangerous[:5]),
            })
            continue
        if not _is_complete(torrent):
            warnings.append({
                "hash": torrent_hash,
                "name": str(torrent.get("name") or "Torrent"),
                "reason": f"Torrent incomplet ({_completion_detail(torrent)}) — rangement refusé",
            })
            continue
        try:
            entry = _torrent_plan(torrent, files if isinstance(files, list) else [], root)
        except ValueError as exc:
            warnings.append({"hash": torrent_hash, "name": str(torrent.get("name") or "Torrent"), "reason": str(exc)})
            continue
        if entry is None:
            warnings.append({"hash": torrent_hash, "name": str(torrent.get("name") or "Torrent"), "reason": "Film, série, jeu ou livre non identifié"})
            continue
        candidates.append(entry)
    metadata_candidates = [entry for entry in candidates if entry.get("kind") in {"film", "series"}]
    if metadata_resolver is not None and metadata_candidates:
        async def resolve_entry(entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            metadata = await metadata_resolver.resolve(
                str(entry.get("folder") or entry.get("name") or ""),
                "film" if entry.get("kind") == "film" else "series",
                str(entry.get("folder") or "").rsplit("(", 1)[-1].rstrip(")") if entry.get("kind") == "film" else None,
            )
            return entry, metadata
        resolved = await asyncio.gather(*(resolve_entry(entry) for entry in metadata_candidates))
        filtered_hashes: set[str] = set()
        for entry, metadata in resolved:
            entry["metadata"] = metadata
            entry["confidence"] = metadata.get("confidence", entry.get("confidence", "heuristic"))
            if entry["confidence"] == "ambiguous":
                warnings.append({"hash": entry["hash"], "name": entry["name"], "reason": "Correspondance Jellyfin/TMDB ambiguë — validation manuelle requise"})
                continue
            filtered_hashes.add(entry["hash"])
        candidates = [
            entry for entry in candidates
            if entry.get("kind") not in {"film", "series"} or entry["hash"] in filtered_hashes
        ]
    manifest_paths_by_hash = {
        str(torrent.get("hash") or "").lower(): {
            _path_key(physical_path)
            for _item, physical_path in _torrent_manifest_items(torrent, files, root)
            if physical_path is not None
        }
        for torrent, files in zip(torrents, file_payloads)
    }
    owners: dict[tuple[str, str], list[dict[str, Any]]] = {}
    disk_collisions: set[str] = set()
    for entry in candidates:
        for target_file in entry["targetFiles"]:
            physical_target = _relative_to_qbit_root(entry["targetPath"], target_file, root)
            target_key = _path_key(physical_target or "")
            if (
                inventory
                and physical_target is not None
                and target_key in inventory.path_keys
                and target_key not in manifest_paths_by_hash.get(entry["hash"], set())
                and entry["hash"] not in disk_collisions
            ):
                disk_collisions.add(entry["hash"])
                warnings.append({
                    "hash": entry["hash"],
                    "name": entry["name"],
                    "reason": f"Collision de destination ({target_file}) — fichier existant non associé au torrent",
                })
            owners.setdefault((entry["targetPath"], target_file.casefold()), []).append(entry)
    colliding_hashes: set[str] = set(disk_collisions)
    for (_location, target_file), target_owners in owners.items():
        distinct = {entry["hash"] for entry in target_owners}
        if len(distinct) <= 1:
            continue
        colliding_hashes.update(distinct)
        names = ", ".join(sorted({entry["name"] for entry in target_owners}))
        warnings.append({"name": names, "reason": f"Collision de destination ({target_file}) — rangement refusé"})
    already_organized = [entry for entry in candidates if entry["alreadyOrganized"]]
    entries = [entry for entry in candidates if not entry["alreadyOrganized"] and entry["hash"] not in colliding_hashes]
    orphan_media = _orphan_from_inventory(inventory, signatures) if inventory and not selected else []
    if mount_root and not selected:
        warnings.extend(_unassociated_from_inventory(inventory, signatures) if inventory else [])
    duplicate_groups = (
        duplicate_groups_from_inventory(inventory)
        if inventory and not selected else []
    )
    torrent_by_file: dict[tuple[str, int], list[dict[str, str]]] = {}
    for torrent, files in zip(torrents, file_payloads):
        torrent_name = str(torrent.get("name") or "")
        for item in files if isinstance(files, list) else []:
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            torrent_by_file.setdefault((posixpath.basename(str(item.get("name") or "")).casefold(), size), []).append({"hash": str(torrent.get("hash") or "").lower(), "name": torrent_name})
    for group in duplicate_groups:
        associated: dict[str, str] = {}
        for item in group["files"]:
            for torrent in torrent_by_file.get((posixpath.basename(item["name"]).casefold(), int(item["size"])), []):
                associated[torrent["hash"]] = torrent["name"]
        group["associatedTorrents"] = [{"hash": key, "name": value} for key, value in sorted(associated.items())]
    result = {
        "sourceFingerprint": source_fingerprint,
        "entries": entries,
        "alreadyOrganized": already_organized,
        "orphanMedia": orphan_media,
        "missingTorrents": missing_torrents,
        "collisions": [warning for warning in warnings if "Collision" in str(warning.get("reason", ""))],
        "ambiguous": [warning for warning in warnings if "ambigu" in str(warning.get("reason", "")).casefold()],
        "ignored": [warning for warning in warnings if warning not in missing_torrents],
        "warnings": warnings,
        "count": len(entries),
        "warningCount": len(warnings),
        "totalOperations": sum(len(entry.get("operations", [])) for entry in entries),
        "totalBytes": total_bytes,
        "duplicateGroups": duplicate_groups,
        "duplicateSummary": {
            "groups": len(duplicate_groups),
            "exactFiles": sum(int(group.get("exactFiles", 0)) for group in duplicate_groups),
            "conflicts": sum(int(group.get("conflicts", 0)) for group in duplicate_groups),
            "recoverableBytes": sum(int(group.get("recoverableBytes", 0)) for group in duplicate_groups),
        },
    }
    logger.info(
        "organize phase=plan duration_ms=%.1f torrents=%d entries=%d orphans=%d duplicates=%d warnings=%d",
        (time.perf_counter() - started_at) * 1000,
        len(torrents), len(entries), len(orphan_media), len(duplicate_groups), len(warnings),
    )
    return result


def _temporary_path(torrent_hash: str, index: int, old_path: str) -> str:
    """Return a unique hidden path beside the original file or folder.

    Keeping the temporary entry in the same directory avoids qBittorrent
    losing ownership when the destination lives on a different filesystem.
    """
    parent = posixpath.dirname(old_path)
    basename = posixpath.basename(old_path)
    extension = posixpath.splitext(basename)[1]
    # Un renommage temporaire doit rester dans un dossier qui existe déjà.
    # Créer une arborescence cachée imbriquée ajoute des parents absents et
    # une mutation supplémentaire avant que qBittorrent ait confirmé la
    # première.  Le nom court évite aussi de dépasser la limite des 255 octets
    # d'un composant.
    temporary_name = f".dashboard-organize-{torrent_hash[:12]}-{index:05d}{extension}"
    return posixpath.join(parent, temporary_name) if parent else temporary_name


def _normalized_paths(payload: Any) -> set[str]:
    """Return safe relative paths from a qBittorrent files payload."""
    paths: set[str] = set()
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            paths.add(_safe_relative(name))
        except ValueError:
            continue
    return paths


def _targets_collide(operations: list[dict[str, Any]], current_paths: set[str]) -> bool:
    """Detect whether a direct rename would land on an existing torrent path.

    Only paths currently owned by the same torrent are relevant here.  When a
    destination is already occupied, a staged rename is required to free it
    first without overwriting anything.
    """
    for operation in operations:
        old_path = _safe_relative(str(operation.get("oldPath") or ""))
        new_path = _safe_relative(str(operation.get("newPath") or ""))
        if new_path in current_paths:
            return True
        for path in current_paths:
            if path == old_path or path.startswith(old_path + "/"):
                continue
            if path == new_path or path.startswith(new_path + "/"):
                return True
    return False


def _expected_paths(
    paths: set[str],
    path_type: str,
    old_path: str,
    new_path: str,
) -> set[str] | None:
    """Project qBittorrent-owned paths after one rename operation."""
    if path_type == "folder":
        if not any(path == old_path or path.startswith(old_path + "/") for path in paths):
            return None
        return {
            new_path + path[len(old_path):] if path == old_path or path.startswith(old_path + "/") else path
            for path in paths
        }
    if old_path not in paths:
        return None
    return (paths - {old_path}) | {new_path}


async def _wait_for_rename(
    qbit: Any,
    torrent_hash: str,
    path_type: str,
    old_path: str,
    new_path: str,
    *,
    timeout_seconds: float = 20.0,
    required: bool = True,
) -> None:
    """Wait until qBittorrent advertises a queued rename as applied.

    ``renameFile``/``renameFolder`` update the torrent metadata on the next
    file-renamed alert.  Chaining a second rename immediately can therefore
    select the old path and be answered with 409.  Clients that cannot list
    torrent files skip the wait.
    """
    if not callable(getattr(qbit, "files", None)):
        return
    try:
        payload = await qbit.files(torrent_hash)
    except QbitError:
        return
    expected = _expected_paths(_normalized_paths(payload), path_type, old_path, new_path)
    if expected is None:
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = _normalized_paths(payload)
        if expected <= current:
            return
        await asyncio.sleep(0.1)
        try:
            payload = await qbit.files(torrent_hash)
        except QbitError:
            return
    if required:
        raise QbitError(409, "qBittorrent n'a pas confirmé le renommage — rangement annulé.")
    logger.warning("organize: rename not confirmed by qBittorrent (%s -> %s)", old_path, new_path)


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
    # Restore through collision-safe temporary names.  A direct reverse loop
    # fails for swaps such as A->B, B->A because both original names remain
    # occupied until the other rename has completed.
    staged: list[tuple[str, str, str]] = []
    for index, (path_type, current, original) in enumerate(renamed):
        rollback_path = _temporary_path(entry["hash"], 100_000 + index, current)
        try:
            await _rename_path(qbit, entry["hash"], path_type, current, rollback_path)
            await _wait_for_rename(qbit, entry["hash"], path_type, current, rollback_path, required=False)
            staged.append((path_type, rollback_path, original))
        except QbitError as exc:
            errors.append(f"{current}: {exc.public_message}")
    for path_type, rollback_path, original in staged:
        try:
            await _rename_path(qbit, entry["hash"], path_type, rollback_path, original)
            await _wait_for_rename(qbit, entry["hash"], path_type, rollback_path, original, required=False)
        except QbitError as exc:
            errors.append(f"{rollback_path}: {exc.public_message}")
    return errors


async def _apply_renames(
    qbit: Any,
    torrent_hash: str,
    operations: list[dict[str, Any]],
    renamed: list[tuple[str, str, str]] | None = None,
) -> list[tuple[str, str, str]]:
    """Apply renames directly when possible, staging only real collisions.

    A single-file movie or a simple episode move has no destination already
    present in qBittorrent metadata.  Renaming straight to the final path is
    both faster and safer: it removes the temporary hidden hop that used to be
    created inside directories that did not exist yet.  Staging is reserved
    for the rare case where a target path is already owned by the torrent.
    """
    try:
        payload = await qbit.files(torrent_hash)
    except QbitError:
        payload = None
    current_paths = _normalized_paths(payload)
    renamed = renamed if renamed is not None else []
    if not _targets_collide(operations, current_paths):
        for operation in operations:
            path_type = str(operation.get("type") or "file")
            old_path = _safe_relative(str(operation.get("oldPath") or ""))
            new_path = _safe_relative(str(operation.get("newPath") or ""))
            if old_path == new_path:
                continue
            await _rename_path(qbit, torrent_hash, path_type, old_path, new_path)
            renamed.append((path_type, new_path, old_path))
            await _wait_for_rename(qbit, torrent_hash, path_type, old_path, new_path)
        return renamed

    temporary: list[tuple[str, str, str, str]] = []
    for index, operation in enumerate(operations):
        path_type = str(operation.get("type") or "file")
        old_path = _safe_relative(str(operation.get("oldPath") or ""))
        new_path = _safe_relative(str(operation.get("newPath") or ""))
        if old_path == new_path:
            continue
        temp_path = _temporary_path(torrent_hash, index, old_path)
        await _rename_path(qbit, torrent_hash, path_type, old_path, temp_path)
        renamed.append((path_type, temp_path, old_path))
        await _wait_for_rename(qbit, torrent_hash, path_type, old_path, temp_path)
        temporary.append((path_type, temp_path, new_path, old_path))
    for index, (path_type, temp_path, new_path, old_path) in enumerate(temporary):
        await _rename_path(qbit, torrent_hash, path_type, temp_path, new_path)
        renamed[index] = (path_type, new_path, old_path)
        await _wait_for_rename(qbit, torrent_hash, path_type, temp_path, new_path)
    return renamed


async def apply_organization_plan(
    qbit: Any,
    plan: dict[str, Any],
    resume_manager: "VerifiedResumeManager | None" = None,
    progress: Callable[[dict[str, Any]], Any] | None = None,
    recheck: bool = False,
) -> dict[str, Any]:
    """Apply a plan while keeping every torrent paused.

    ``resume_manager`` remains an optional compatibility argument for callers
    from older versions.  It is intentionally ignored: organizing media must
    never trigger a download or a resume automatically.

    ``recheck`` defaults to False: the renames and the location change are
    performed by qBittorrent itself, so its file database is already
    consistent.  Re-reading every torrent on the (slow) FUSE mount is opt-in.
    """
    results: list[dict[str, Any]] = []
    organized = 0
    entries = plan.get("entries", [])
    total = len(entries)

    async def report(event: dict[str, Any]) -> None:
        if progress is None:
            return
        try:
            outcome = progress(event)
            if inspect.isawaitable(outcome):
                await outcome
        except Exception:
            logger.exception("organization progress callback failed")

    for index, entry in enumerate(entries, start=1):
        torrent_hash = entry["hash"]
        torrent_name = str(entry.get("name") or "Torrent")
        renamed: list[tuple[str, str, str]] = []
        location_changed = False
        current_stage = "pausing"
        try:
            await report({
                "stage": "pausing",
                "index": index,
                "total": total,
                "processed": organized,
                "hash": torrent_hash,
                "name": torrent_name,
                "message": "Mise en pause avant toute mutation.",
            })
            await qbit.pause_many([torrent_hash])
            await _wait_until_paused(qbit, torrent_hash)
            current_stage = "moving"
            await report({
                "stage": "moving",
                "index": index,
                "total": total,
                "processed": organized,
                "hash": torrent_hash,
                "name": torrent_name,
                "message": "Pause confirmée — renommage et déplacement en cours.",
            })
            renamed = await _apply_renames(qbit, torrent_hash, entry["operations"], renamed)
            if entry["currentPath"] != entry["targetPath"]:
                await qbit.set_location_many([torrent_hash], entry["targetPath"])
                location_changed = True
            if recheck:
                current_stage = "rechecking"
                await report({
                    "stage": "rechecking",
                    "index": index,
                    "total": total,
                    "processed": organized,
                    "hash": torrent_hash,
                    "name": torrent_name,
                    "message": "Déplacement effectué — revérification demandée.",
                })
                await qbit.recheck_many([torrent_hash])
                current_stage = "verification"
                await report({
                    "stage": "verification",
                    "index": index,
                    "total": total,
                    "processed": organized,
                    "hash": torrent_hash,
                    "name": torrent_name,
                    "message": "Vérification qBittorrent demandée.",
                })
            else:
                current_stage = "verification"
                await report({
                    "stage": "verification",
                    "index": index,
                    "total": total,
                    "processed": organized,
                    "hash": torrent_hash,
                    "name": torrent_name,
                    "message": "Déplacement effectué — vérification ignorée (métadonnées qBittorrent à jour).",
                })
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
            await report({
                "stage": "failed",
                "index": index,
                "total": total,
                "processed": organized,
                "hash": torrent_hash,
                "name": torrent_name,
                "message": exc.public_message,
                "failedStage": current_stage,
            })
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
        # Kept as a compatibility no-op for older callers.  Organization
        # never schedules a resume anymore.
        return None

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
        # Deliberately does nothing.  A verified torrent is still paused and
        # can only be resumed by an explicit user action.
        return None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                await self.check_once()
            except Exception:
                logger.exception("Verified resume check failed")

    def snapshot(self) -> dict[str, Any]:
        return {"pending": len(self._pending), "hashes": [value[:8] for value in self._pending]}
