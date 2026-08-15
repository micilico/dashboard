from __future__ import annotations

import hashlib
import os
import posixpath
import re

from ..config import MOUNT_PATH
from ..security import resolve_path_within
from ..storage import clear_scandir_cache, create_directory, delete_item, move_item, rename_item

_SEASON_RE = re.compile(
    r"(?i)\bS(\d{1,2})(?:E\d{1,3}){0,2}\b"
    r"|\bSeason[\s._-]*(\d{1,2})\b"
    r"|\bSaison[\s._-]*(\d{1,2})\b"
    r"|\b(Special(?:s)?)\b"
)
_EPISODE_RE = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
_TRAILING_SEPARATORS_RE = re.compile(r"[\s._-]+$")
_YEAR_RE = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
_QUALITY_RE = re.compile(
    r"(?i)(?:1080p|2160p|720p|480p|576p|4k|uhd|bluray|brrip|bdrip|webrip|web-dl|"
    r"webdl|hdtv|remux|hdr|dolbyvision|x264|x265|h264|h265|hevc|avc|dvdrip|proper|repack)"
)
_PARASITE_EXTS = {".txt", ".nfo", ".rar", ".sfv", ".md5", ".url", ".crc", ".log"}
_SAMPLE_RE = re.compile(r"(?i)(?:^|[\s._-])sample(?:[\s._-]|$)")
_FILMS_DIRNAMES = {"films", "filmes", "film", "movie", "movies", "videos", "cinema", "movies_hd", "film_hd"}
_SERIES_DIRNAMES = {"séries", "series", "serie", "tv", "shows", "tv-shows", "tv_shows", "saisons", "seasons"}
_QB_ROOT = "qbittorrent"
_QB_FILMS = "qbittorrent/Films"
_QB_SERIES = "qbittorrent/Series"


def is_qbittorrent_tree(relative_path: str) -> bool:
    """Return whether a path is owned by qBittorrent's coordinated organizer."""
    normalized = posixpath.normpath(str(relative_path or ".").replace("\\", "/")).strip("/")
    return normalized.casefold() == _QB_ROOT or normalized.casefold().startswith(_QB_ROOT + "/")


def _rel(path: str, base: str) -> str:
    return os.path.relpath(path, base).replace(os.sep, "/")


def normalize_series_name(raw: str) -> str:
    words = re.split(r"[.\s_\-]+", raw.strip())
    words = [word for word in words if word]
    if not words:
        return raw.strip()
    return " ".join(word[:1].upper() + word[1:].lower() if word[0].isalpha() else word for word in words)


def extract_series_name(name: str) -> str | None:
    if not isinstance(name, str) or not name:
        return None
    match = _SEASON_RE.search(name)
    if not match:
        return None
    series = _TRAILING_SEPARATORS_RE.sub("", name[: match.start()])
    return series or None


def extract_season_number(name: str) -> int | None:
    if not isinstance(name, str) or not name:
        return None
    match = _SEASON_RE.search(name)
    if not match:
        return None
    for group in (1, 2, 3):
        if match.group(group) is not None:
            return int(match.group(group))
    return 0 if match.group(4) is not None else None


def extract_episode_number(name: str) -> tuple[int, int] | None:
    match = _EPISODE_RE.search(name or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def season_folder_label(season: int) -> str:
    return "Specials" if season == 0 else f"Saison {season}"


def _movie_signature(name: str) -> tuple[str, str] | None:
    if not isinstance(name, str) or not name or _SEASON_RE.search(name):
        return None
    match = _YEAR_RE.search(name)
    if not match:
        return None
    title = normalize_series_name(re.sub(r"^[\W_]+|[\W_]+$", "", name[: match.start()]))
    return (title, match.group()) if title else None


def extract_movie(name: str) -> dict | None:
    if not isinstance(name, str) or not name or _SEASON_RE.search(name):
        return None
    if not (_QUALITY_RE.search(name) or re.search(r"\(\s*(?:19\d{2}|20\d{2})\s*\)\s*$", name)):
        return None
    signature = _movie_signature(name)
    return {"title": signature[0], "year": signature[1]} if signature else None


def detect_parasite(name: str) -> str | None:
    if not isinstance(name, str) or not name:
        return None
    if _SAMPLE_RE.search(name) or name.lower().startswith("sample"):
        return "sample"
    extension = os.path.splitext(name)[1].lower()
    return extension[1:] if extension in _PARASITE_EXTS else None


def _find_category_dir(mount_real: str, names: set[str]) -> str | None:
    try:
        for entry in os.scandir(mount_real):
            if entry.is_dir() and entry.name.casefold() in names:
                return entry.name
    except OSError:
        pass
    return None


def _inside(relative_path: str, category: str | None) -> bool:
    return bool(category and (relative_path == category or relative_path.startswith(category + "/")))


def _categories(relative_path: str, target_dir: str) -> dict:
    normalized_path = posixpath.normpath(relative_path or ".")
    if normalized_path == _QB_ROOT or normalized_path.startswith(_QB_ROOT + "/"):
        current_name = os.path.basename(os.path.normpath(target_dir)).casefold()
        in_series = current_name in _SERIES_DIRNAMES or normalized_path == _QB_SERIES or normalized_path.startswith(_QB_SERIES + "/")
        in_films = current_name in _FILMS_DIRNAMES or normalized_path == _QB_FILMS or normalized_path.startswith(_QB_FILMS + "/")
        return {"series": _QB_SERIES, "films": _QB_FILMS, "series_name": "Series", "films_name": "Films", "in_place_series": in_series, "in_place_films": in_films}
    mount_real = os.path.realpath(MOUNT_PATH)
    series_name = _find_category_dir(mount_real, _SERIES_DIRNAMES)
    films_name = _find_category_dir(mount_real, _FILMS_DIRNAMES)
    current_name = os.path.basename(os.path.normpath(target_dir)).casefold()
    in_series = current_name in _SERIES_DIRNAMES or _inside(relative_path, series_name)
    in_films = current_name in _FILMS_DIRNAMES or _inside(relative_path, films_name)
    return {
        "series": relative_path if in_series else (series_name or "séries"),
        "films": relative_path if in_films else (films_name or "films"),
        "series_name": series_name or "séries",
        "films_name": films_name or "films",
        "in_place_series": in_series,
        "in_place_films": in_films,
    }


def _existing_named_dir(parent: str, name: str, *, normalize: bool = False) -> str | None:
    wanted = normalize_series_name(name).casefold() if normalize else name.casefold()
    try:
        for entry in os.scandir(parent):
            if not entry.is_dir():
                continue
            candidate = normalize_series_name(entry.name).casefold() if normalize else entry.name.casefold()
            if candidate == wanted:
                return entry.name
    except OSError:
        pass
    return None


def _find_existing_season_dir(series_abs: str, season: int) -> str | None:
    try:
        for entry in os.scandir(series_abs):
            if entry.is_dir() and extract_season_number(entry.name) == season:
                return entry.name
    except OSError:
        pass
    return None


def _find_existing_movie_dir(films_parent_abs: str, title: str, year: str, exclude: str | None = None) -> str | None:
    try:
        for entry in os.scandir(films_parent_abs):
            if not entry.is_dir() or entry.name == exclude:
                continue
            signature = _movie_signature(entry.name)
            if signature and signature[0].casefold() == title.casefold() and signature[1] == year:
                return entry.name
    except OSError:
        pass
    return None


def _ensure_nested_dir(relative_path: str) -> None:
    current = ""
    for part in relative_path.split("/"):
        if not part:
            continue
        next_path = posixpath.join(current, part)
        if not os.path.isdir(resolve_path_within(MOUNT_PATH, next_path, must_exist=False)):
            create_directory(current, part)
        current = next_path


def _file_map(directory: str) -> dict[str, tuple[int, tuple[int, int] | None]]:
    result: dict[str, tuple[int, tuple[int, int] | None]] = {}
    if not os.path.isdir(directory):
        return result
    for entry in os.scandir(directory):
        if entry.is_file():
            try:
                result[entry.name] = (entry.stat().st_size, extract_episode_number(entry.name))
            except OSError:
                pass
    return result


def _same_file(left: str, right: str) -> bool:
    """Require equal content before treating a duplicate as safe to remove."""
    try:
        if os.path.getsize(left) != os.path.getsize(right):
            return False
        left_hash = hashlib.sha256()
        right_hash = hashlib.sha256()
        with open(left, "rb") as left_stream, open(right, "rb") as right_stream:
            while True:
                left_chunk = left_stream.read(1024 * 1024)
                right_chunk = right_stream.read(1024 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return left_hash.digest() == right_hash.digest()
                left_hash.update(left_chunk)
                right_hash.update(right_chunk)
    except OSError:
        return False


def _duplicate_entries(src_dir: str, dest_dir: str, source: str, target: str, kind: str) -> list[dict]:
    source_files = _file_map(src_dir)
    target_files = _file_map(dest_dir)
    target_episodes = {episode: name for name, (_size, episode) in target_files.items() if episode}
    duplicates = []
    for name, (size, episode) in source_files.items():
        if name in target_files:
            status = "identique" if _same_file(os.path.join(src_dir, name), os.path.join(dest_dir, name)) else "conflit"
            duplicates.append({"kind": kind, "source": source, "target": target, "file": name, "status": status})
        elif episode and episode in target_episodes:
            duplicates.append({"kind": kind, "source": source, "target": target, "file": name, "status": "épisode similaire"})
    return duplicates


def _find_parasites(directory: str, prefix: str) -> list[dict]:
    found = []
    for root, _dirs, files in os.walk(directory):
        for filename in files:
            reason = detect_parasite(filename)
            if reason:
                found.append({"path": f"{prefix}/{_rel(os.path.join(root, filename), directory)}", "reason": reason})
    return found


def build_organization_plan(relative_path: str) -> dict:
    target_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(target_dir):
        raise ValueError("Dossier introuvable")
    categories = _categories(relative_path, target_dir)
    mount_real = os.path.realpath(MOUNT_PATH)
    series_parent = resolve_path_within(MOUNT_PATH, categories["series"], must_exist=False)
    films_parent = resolve_path_within(MOUNT_PATH, categories["films"], must_exist=False)
    series_groups: dict[str, dict] = {}
    movies: list[dict] = []
    parasites: list[dict] = []
    duplicates: list[dict] = []

    for entry in os.scandir(target_dir):
        is_dir = entry.is_dir()
        source = _rel(entry.path, mount_real)
        reason = None if is_dir else detect_parasite(entry.name)
        if reason:
            parasites.append({"path": source, "reason": reason})
            continue
        series = extract_series_name(entry.name)
        if series:
            normalized = normalize_series_name(series)
            key = normalized.casefold()
            season = extract_season_number(entry.name) or 0
            if key not in series_groups:
                existing_series = _existing_named_dir(series_parent, normalized, normalize=True)
                series_groups[key] = {
                    "name": normalized,
                    "folder_exists": existing_series is not None,
                    "existing_series": existing_series,
                    "items": [],
                }
            group = series_groups[key]
            series_dir_name = group["existing_series"] or normalized
            series_abs = resolve_path_within(MOUNT_PATH, posixpath.join(categories["series"], series_dir_name), must_exist=False)
            existing_season = _find_existing_season_dir(series_abs, season)
            season_dir = existing_season or season_folder_label(season)
            target = posixpath.join(categories["series"], series_dir_name, season_dir)
            item = {"name": entry.name, "is_dir": is_dir, "season": season, "target": target}
            group["items"].append(item)
            destination = resolve_path_within(MOUNT_PATH, target, must_exist=False)
            if is_dir:
                duplicates.extend(_duplicate_entries(entry.path, destination, source, target, "série"))
                parasites.extend(_find_parasites(entry.path, source))
            elif os.path.isdir(destination):
                source_size = entry.stat().st_size
                target_files = _file_map(destination)
                if entry.name in target_files:
                    status = "identique" if _same_file(entry.path, os.path.join(destination, entry.name)) else "conflit"
                    duplicates.append({"kind": "série", "source": source, "target": target, "file": entry.name, "status": status})
                episode = extract_episode_number(entry.name)
                if episode and any(info[1] == episode and filename != entry.name for filename, info in target_files.items()):
                    duplicates.append({"kind": "série", "source": source, "target": target, "file": entry.name, "status": "épisode similaire"})
            continue
        movie = extract_movie(entry.name)
        if movie:
            folder = f"{movie['title']} ({movie['year']})"
            if not is_dir:
                folder += os.path.splitext(entry.name)[1]
            existing_movie = _find_existing_movie_dir(films_parent, movie["title"], movie["year"], exclude=entry.name)
            target = posixpath.join(categories["films"], existing_movie or folder)
            movies.append({"name": entry.name, "is_dir": is_dir, "folder": folder, "target": target})
            destination = resolve_path_within(MOUNT_PATH, target, must_exist=False)
            duplicates.extend(_duplicate_entries(entry.path, destination, source, target, "film"))
            continue

    series_list = sorted(series_groups.values(), key=lambda group: group["name"].casefold())
    for group in series_list:
        group["items"].sort(key=lambda item: (item["season"], item["name"].casefold()))
    return {
        "categories": categories,
        "series": series_list,
        "movies": movies,
        "parasites": parasites,
        "duplicates": duplicates,
        "totals": {
            "series": len(series_list),
            "series_items": sum(len(group["items"]) for group in series_list),
            "movies": len(movies),
            "parasites": len(parasites),
            "duplicates": len(duplicates),
        },
    }


def _move_children_deduplicated(src_dir: str, dest_dir_rel: str, source: str, kind: str, errors: list[str]) -> tuple[int, int]:
    dest_dir = resolve_path_within(MOUNT_PATH, dest_dir_rel, must_exist=True)
    existing = _file_map(dest_dir)
    source_parent_rel = _rel(src_dir, os.path.realpath(MOUNT_PATH))
    moved = skipped = 0
    for child in list(os.scandir(src_dir)):
        if not child.is_file():
            continue
        try:
            size = child.stat().st_size
        except OSError:
            continue
        if child.name in existing:
            if not _same_file(child.path, os.path.join(dest_dir, child.name)):
                errors.append(f"{source}/{child.name} : conflit de contenu")
            else:
                try:
                    delete_item(source_parent_rel, child.name, permanent=False)
                except OSError:
                    pass
            skipped += 1
            continue
        episode = extract_episode_number(child.name)
        if episode and any(info[1] == episode for info in existing.values()):
            errors.append(f"{source}/{child.name} : épisode déjà présent (autre qualité)")
            skipped += 1
            continue
        try:
            move_item(source_parent_rel, child.name, dest_dir_rel)
            moved += 1
            existing[child.name] = (size, episode)
        except ValueError as exc:
            errors.append(f"{source}/{child.name} : {exc}")
    return moved, skipped


def _remove_empty_dir(path: str) -> None:
    try:
        if not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


def _flatten_season(src_parent_rel: str, name: str, series_rel: str, season_rel: str, errors: list[str]) -> tuple[int, int]:
    src_parent = resolve_path_within(MOUNT_PATH, src_parent_rel, must_exist=True)
    src_abs = os.path.join(src_parent, name)
    target_abs = resolve_path_within(MOUNT_PATH, season_rel, must_exist=False)
    if not os.path.exists(target_abs):
        move_item(src_parent_rel, name, series_rel)
        rename_item(series_rel, name, os.path.basename(season_rel))
        return 1, 0
    moved, skipped = _move_children_deduplicated(src_abs, season_rel, name, "série", errors)
    _remove_empty_dir(src_abs)
    return moved, skipped


def _place_loose(src_parent_rel: str, name: str, season_rel: str, errors: list[str]) -> tuple[int, int]:
    parent = resolve_path_within(MOUNT_PATH, src_parent_rel, must_exist=True)
    target_dir = resolve_path_within(MOUNT_PATH, season_rel, must_exist=False)
    if not os.path.isdir(target_dir):
        _ensure_nested_dir(season_rel)
    target_file = os.path.join(target_dir, name)
    if os.path.exists(target_file):
        source_size = os.path.getsize(os.path.join(parent, name))
        target_size = os.path.getsize(target_file)
        if source_size != target_size:
            errors.append(f"{name} : conflit de contenu")
        elif source_size == target_size and _same_file(os.path.join(parent, name), target_file):
            try:
                delete_item(src_parent_rel, name, permanent=False)
            except OSError:
                pass
        else:
            errors.append(f"{name} : conflit de contenu")
        return 0, 1
    episode = extract_episode_number(name)
    target_files = _file_map(target_dir)
    if episode and any(info[1] == episode for info in target_files.values()):
        errors.append(f"{name} : épisode déjà présent (autre qualité)")
        return 0, 1
    move_item(src_parent_rel, name, season_rel)
    return 1, 0


def _merge_movie(src_parent_rel: str, name: str, destination_rel: str, is_dir: bool, errors: list[str]) -> tuple[int, int]:
    parent = resolve_path_within(MOUNT_PATH, src_parent_rel, must_exist=True)
    source_abs = os.path.join(parent, name)
    destination = resolve_path_within(MOUNT_PATH, destination_rel, must_exist=False)
    if not is_dir:
        if os.path.isdir(destination):
            existing = _file_map(destination)
            size = os.path.getsize(source_abs)
            if name in existing:
                if existing[name][0] != size:
                    errors.append(f"{name} : conflit de contenu")
                elif _same_file(source_abs, os.path.join(destination, name)):
                    try:
                        delete_item(src_parent_rel, name, permanent=False)
                    except OSError:
                        pass
                else:
                    errors.append(f"{name} : conflit de contenu")
                return 0, 1
            try:
                move_item(src_parent_rel, name, destination_rel)
                return 1, 0
            except ValueError as exc:
                errors.append(f"{name} : {exc}")
                return 0, 0
        if os.path.exists(destination):
            source_size = os.path.getsize(source_abs)
            target_size = os.path.getsize(destination)
            if source_size != target_size or not _same_file(source_abs, destination):
                errors.append(f"{name} : conflit de contenu")
            else:
                try:
                    delete_item(src_parent_rel, name, permanent=False)
                except OSError:
                    pass
            return 0, 1
        destination_parent = posixpath.dirname(destination_rel)
        if destination_parent == src_parent_rel:
            rename_item(src_parent_rel, name, os.path.basename(destination_rel))
            return 1, 0
        _ensure_nested_dir(posixpath.dirname(destination_rel))
        move_item(src_parent_rel, name, destination_parent)
        if os.path.basename(destination_rel) != name:
            rename_item(posixpath.dirname(destination_rel), name, os.path.basename(destination_rel))
        return 1, 0
    if not os.path.exists(destination):
        destination_parent = posixpath.dirname(destination_rel)
        if destination_parent == src_parent_rel:
            rename_item(src_parent_rel, name, os.path.basename(destination_rel))
            return 1, 0
        _ensure_nested_dir(posixpath.dirname(destination_rel))
        move_item(src_parent_rel, name, destination_parent)
        if os.path.basename(destination_rel) != name:
            rename_item(posixpath.dirname(destination_rel), name, os.path.basename(destination_rel))
        return 1, 0
    moved, skipped = _move_children_deduplicated(source_abs, destination_rel, name, "film", errors)
    _remove_empty_dir(source_abs)
    return moved, skipped


def apply_organization_plan(relative_path: str) -> dict:
    plan = build_organization_plan(relative_path)
    target_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    categories = plan["categories"]
    errors: list[str] = []
    created_series: list[str] = []
    renamed_series: list[str] = []
    series_moved = duplicates_skipped = movies_moved = 0

    if not categories["in_place_series"] and plan["series"]:
        _ensure_nested_dir(categories["series"])
    if not categories["in_place_films"] and plan["movies"]:
        _ensure_nested_dir(categories["films"])

    series_parent = None
    if plan["series"]:
        series_parent = resolve_path_within(MOUNT_PATH, categories["series"], must_exist=True)
    for group in plan["series"]:
        normalized = group["name"]
        existing = _existing_named_dir(series_parent, normalized, normalize=True)
        if existing is None:
            create_directory(categories["series"], normalized)
            existing = normalized
            created_series.append(normalized)
        elif existing != normalized:
            try:
                rename_item(categories["series"], existing, normalized)
                existing = normalized
                renamed_series.append(normalized)
            except ValueError as exc:
                errors.append(f"{existing} → {normalized} : {exc}")
        series_rel = posixpath.join(categories["series"], existing)
        series_abs = resolve_path_within(MOUNT_PATH, series_rel, must_exist=True)
        for item in group["items"]:
            existing_season = _find_existing_season_dir(series_abs, item["season"])
            season_rel = posixpath.join(series_rel, existing_season or season_folder_label(item["season"]))
            try:
                if item["is_dir"]:
                    moved, skipped = _flatten_season(relative_path, item["name"], series_rel, season_rel, errors)
                else:
                    moved, skipped = _place_loose(relative_path, item["name"], season_rel, errors)
                series_moved += moved
                duplicates_skipped += skipped
            except (ValueError, OSError) as exc:
                errors.append(f"{item['name']} : {exc}")

    for movie in plan["movies"]:
        try:
            moved, skipped = _merge_movie(relative_path, movie["name"], movie["target"], movie["is_dir"], errors)
            movies_moved += moved
            duplicates_skipped += skipped
        except (ValueError, OSError) as exc:
            errors.append(f"{movie['name']} : {exc}")

    clear_scandir_cache()
    return {
        "success": True,
        "created_series": created_series,
        "renamed_series": renamed_series,
        "series_count": len(plan["series"]),
        "series_moved": series_moved,
        "movies_moved": movies_moved,
        "duplicates_skipped": duplicates_skipped,
        "duplicates_signaled": len(plan["duplicates"]),
        "errors": errors,
        "totals": plan["totals"],
    }
