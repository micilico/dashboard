from __future__ import annotations

import os
import posixpath
import re

from ..config import MOUNT_PATH
from ..security import resolve_path_within
from ..storage import clear_scandir_cache, create_directory, move_item, rename_item

_SEASON_RE = re.compile(
    r"(?i)\bS(\d{1,2})(?:E\d{1,3}){0,2}\b"
    r"|\bSeason[\s._-]*(\d{1,2})\b"
    r"|\bSaison[\s._-]*(\d{1,2})\b"
    r"|\b(Special(?:s)?)\b"
)
_TRAILING_SEPARATORS_RE = re.compile(r"[\s._-]+$")
_YEAR_RE = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
_QUALITY_RE = re.compile(
    r"(?i)(?:1080p|2160p|720p|480p|576p|4k|uhd|bluray|brrip|bdrip|webrip|web-dl|"
    r"webdl|hdtv|remux|hdr|dolbyvision|x264|x265|h264|h265|hevc|avc|dvdrip|proper|repack)"
)
_PARASITE_EXTS = {".txt", ".nfo", ".rar", ".sfv", ".md5", ".url", ".crc", ".log"}
_SAMPLE_RE = re.compile(r"(?i)(?:^|[\s._-])sample(?:[\s._-]|$)")
_FILMS_DIRNAMES = {
    "films", "filmes", "film", "movie", "movies", "videos", "cinema", "movies_hd", "film_hd",
}


def _rel(path: str, base: str) -> str:
    return os.path.relpath(path, base).replace(os.sep, "/")


def normalize_series_name(raw: str) -> str:
    """Clean a scene-style name: dots/underscores/dashes to spaces, Title Case."""
    words = re.split(r"[.\s_\-]+", raw.strip())
    words = [w for w in words if w]
    if not words:
        return raw.strip()
    out = []
    for w in words:
        if w[0].isalpha():
            out.append(w[0].upper() + w[1:].lower())
        else:
            out.append(w)
    return " ".join(out)


def extract_series_name(name: str) -> str | None:
    """Return the raw series name preceding the season marker, or None."""
    if not isinstance(name, str) or not name:
        return None
    match = _SEASON_RE.search(name)
    if not match:
        return None
    series = _TRAILING_SEPARATORS_RE.sub("", name[: match.start()])
    if not series:
        return None
    return series


def extract_season_number(name: str) -> int | None:
    """Extract the season number from a season folder name (Specials -> 0)."""
    if not isinstance(name, str) or not name:
        return None
    match = _SEASON_RE.search(name)
    if not match:
        return None
    for group in (1, 2, 3):
        if match.group(group) is not None:
            return int(match.group(group))
    if match.group(4) is not None:
        return 0
    return None


def season_folder_label(season: int) -> str:
    return "Specials" if season == 0 else f"Saison {season}"


def extract_movie(name: str) -> dict | None:
    """Detect a movie (year + quality or parenthesized year), never a series season."""
    if not isinstance(name, str) or not name:
        return None
    if _SEASON_RE.search(name):
        return None
    match = _YEAR_RE.search(name)
    if not match:
        return None
    year = match.group()
    parenthesized = bool(re.search(r"\(\s*(?:19\d{2}|20\d{2})\s*\)\s*$", name))
    if not (_QUALITY_RE.search(name) or parenthesized):
        return None
    title_raw = re.sub(r"^[\W_]+|[\W_]+$", "", name[: match.start()])
    title = normalize_series_name(title_raw)
    if not title:
        return None
    return {"title": title, "year": year}


def detect_parasite(name: str) -> str | None:
    """Report junk files (samples, nfo, rar...) that should not be moved."""
    if not isinstance(name, str) or not name:
        return None
    if _SAMPLE_RE.search(name) or name.lower().startswith("sample"):
        return "sample"
    ext = os.path.splitext(name)[1].lower()
    if ext in _PARASITE_EXTS:
        return ext.lstrip(".")
    return None


def _existing_series_dir(target_dir: str, norm_name: str) -> str | None:
    key = norm_name.casefold()
    try:
        for entry in os.scandir(target_dir):
            if entry.is_dir() and normalize_series_name(entry.name).casefold() == key:
                return entry.name
    except OSError:
        pass
    return None


def _is_films_dirname(basename: str) -> bool:
    return basename.casefold() in _FILMS_DIRNAMES


def _find_parasites(season_dir: str, prefix: str) -> list[dict]:
    found: list[dict] = []
    for root, _dirs, files in os.walk(season_dir):
        for fname in files:
            reason = detect_parasite(fname)
            if reason:
                rel = _rel(os.path.join(root, fname), season_dir)
                found.append({"path": f"{prefix}/{rel}", "reason": reason})
    return found


def build_organization_plan(relative_path: str) -> dict:
    """Scan the directory and build a reorganization plan without moving anything."""
    target_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(target_dir):
        raise ValueError("Dossier introuvable")
    is_films_dir = _is_films_dirname(os.path.basename(os.path.normpath(target_dir)))
    mount_real = os.path.realpath(MOUNT_PATH)

    series_groups: dict[str, dict] = {}
    movies: list[dict] = []
    parasites: list[dict] = []

    for entry in os.scandir(target_dir):
        try:
            is_dir = entry.is_dir()
        except OSError:
            is_dir = False
        prefix = _rel(entry.path, mount_real)

        if not is_dir and detect_parasite(entry.name):
            parasites.append({"path": prefix, "reason": detect_parasite(entry.name)})
            continue

        series = extract_series_name(entry.name)
        if series:
            norm = normalize_series_name(series)
            key = norm.casefold()
            season = extract_season_number(entry.name)
            label = season_folder_label(season if season is not None else 1)
            if key not in series_groups:
                series_groups[key] = {
                    "name": norm,
                    "folder_exists": _existing_series_dir(target_dir, norm) is not None,
                    "items": [],
                }
            series_groups[key]["items"].append({
                "name": entry.name,
                "is_dir": is_dir,
                "season": season if season is not None else 1,
                "target": f"{norm}/{label}",
            })
            if is_dir:
                parasites.extend(_find_parasites(entry.path, prefix))
            continue

        movie = extract_movie(entry.name)
        if movie:
            folder = f"{movie['title']} ({movie['year']})"
            if not is_dir:
                folder += os.path.splitext(entry.name)[1]
            dest = "" if is_films_dir else "Films"
            target = f"{dest}/{folder}" if dest else folder
            movies.append({
                "name": entry.name,
                "is_dir": is_dir,
                "folder": folder,
                "dest": dest,
                "target": target,
            })

    series_list = sorted(series_groups.values(), key=lambda g: g["name"].casefold())
    return {
        "series": series_list,
        "movies": movies,
        "parasites": parasites,
        "totals": {
            "series": len(series_list),
            "series_items": sum(len(g["items"]) for g in series_list),
            "movies": len(movies),
            "parasites": len(parasites),
        },
    }


def _flatten_season(
    src_path: str,
    season_name: str,
    series_rel: str,
    season_rel: str,
    errors: list[str],
) -> int:
    series_abs = resolve_path_within(MOUNT_PATH, series_rel, must_exist=True)
    season_abs = os.path.join(series_abs, os.path.basename(season_rel))
    if not os.path.exists(season_abs):
        move_item(src_path, season_name, series_rel)
        rename_item(series_rel, season_name, os.path.basename(season_rel))
        return 1
    src_abs = os.path.join(resolve_path_within(MOUNT_PATH, src_path, must_exist=True), season_name)
    moved = 0
    for child in os.scandir(src_abs):
        try:
            move_item(series_rel, f"{season_name}/{child.name}", season_rel)
            moved += 1
        except ValueError as exc:
            errors.append(f"{season_name}/{child.name} : {exc}")
    try:
        os.rmdir(src_abs)
    except OSError:
        pass
    return moved


def _place_loose_episode(
    src_path: str,
    name: str,
    series_rel: str,
    season_rel: str,
) -> int:
    series_abs = resolve_path_within(MOUNT_PATH, series_rel, must_exist=True)
    season_label = os.path.basename(season_rel)
    if not os.path.isdir(os.path.join(series_abs, season_label)):
        create_directory(series_rel, season_label)
    move_item(src_path, name, season_rel)
    return 1


def apply_organization_plan(relative_path: str) -> dict:
    """Group seasons into normalized series folders, rename seasons, and move movies."""
    plan = build_organization_plan(relative_path)
    target_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    is_films_dir = _is_films_dirname(os.path.basename(os.path.normpath(target_dir)))

    created_series: list[str] = []
    renamed_series: list[str] = []
    series_items_moved = 0
    errors: list[str] = []

    for group in plan["series"]:
        norm = group["name"]
        existing = _existing_series_dir(target_dir, norm)
        if existing is None:
            try:
                create_directory(relative_path, norm)
                existing = norm
                created_series.append(norm)
            except ValueError as exc:
                errors.append(f"{norm} : {exc}")
                continue
        elif existing != norm:
            try:
                rename_item(relative_path, existing, norm)
                renamed_series.append(norm)
                existing = norm
            except ValueError as exc:
                errors.append(f"{existing} → {norm} : {exc}")
        series_rel = posixpath.join(relative_path, existing)

        for item in group["items"]:
            label = season_folder_label(item["season"])
            season_rel = posixpath.join(series_rel, label)
            try:
                if item["is_dir"]:
                    series_items_moved += _flatten_season(
                        relative_path, item["name"], series_rel, season_rel, errors
                    )
                else:
                    series_items_moved += _place_loose_episode(
                        relative_path, item["name"], series_rel, season_rel
                    )
            except (ValueError, OSError) as exc:
                errors.append(f"{item['name']} : {exc}")

    movies_moved = 0
    for movie in plan["movies"]:
        folder = movie["folder"]
        try:
            if is_films_dir:
                rename_item(relative_path, movie["name"], folder)
                movies_moved += 1
                continue
            films_rel = posixpath.join(relative_path, "Films")
            films_abs = os.path.join(target_dir, "Films")
            if not os.path.isdir(films_abs):
                create_directory(relative_path, "Films")
            target_abs = os.path.join(films_abs, folder)
            if not os.path.exists(target_abs):
                move_item(relative_path, movie["name"], films_rel)
                rename_item(films_rel, movie["name"], folder)
                movies_moved += 1
            elif movie["is_dir"]:
                src_abs = os.path.join(target_dir, movie["name"])
                moved_children = 0
                for child in os.scandir(src_abs):
                    try:
                        move_item(relative_path, f"{movie['name']}/{child.name}", posixpath.join(films_rel, folder))
                        moved_children += 1
                    except ValueError as exc:
                        errors.append(f"{movie['name']}/{child.name} : {exc}")
                try:
                    os.rmdir(src_abs)
                except OSError:
                    pass
                movies_moved += moved_children
            else:
                move_item(relative_path, movie["name"], posixpath.join(films_rel, folder))
                movies_moved += 1
        except (ValueError, OSError) as exc:
            errors.append(f"{movie['name']} : {exc}")

    clear_scandir_cache()
    return {
        "success": True,
        "created_series": created_series,
        "renamed_series": renamed_series,
        "series_count": len(plan["series"]),
        "series_moved": series_items_moved,
        "movies_moved": movies_moved,
        "errors": errors,
        "totals": plan["totals"],
    }
