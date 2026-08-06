from __future__ import annotations

import os
import posixpath
import re

from ..config import MOUNT_PATH
from ..security import resolve_path_within
from ..storage import clear_scandir_cache, create_directory, move_item

_SEASON_RE = re.compile(
    r"(?i)\bS(\d{1,2})(?:E\d{1,3}){0,2}\b"
    r"|\bSeason[\s._-]*\d{1,2}\b"
    r"|\bSaison[\s._-]*\d{1,2}\b"
)
_TRAILING_SEPARATORS_RE = re.compile(r"[\s._-]+$")


def extract_series_name(name: str) -> str | None:
    """Return the series name preceding the season marker, or None."""
    if not isinstance(name, str) or not name:
        return None
    match = _SEASON_RE.search(name)
    if not match:
        return None
    series = _TRAILING_SEPARATORS_RE.sub("", name[: match.start()])
    if not series:
        return None
    return series


def _find_existing_series_dir(target_dir: str, series_name: str) -> str | None:
    wanted = series_name.casefold()
    try:
        for entry in os.scandir(target_dir):
            if entry.is_dir() and entry.name.casefold() == wanted:
                return entry.name
    except OSError:
        pass
    return None


def build_series_plan(relative_path: str) -> dict:
    """Scan the directory and group series seasons without moving anything."""
    target_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(target_dir):
        raise ValueError("Dossier introuvable")

    groups: dict[str, dict] = {}
    for entry in os.scandir(target_dir):
        series = extract_series_name(entry.name)
        if not series:
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            is_dir = False
        key = series.casefold()
        if key not in groups:
            groups[key] = {
                "name": series,
                "items": [],
                "folder_exists": _find_existing_series_dir(target_dir, series) is not None,
            }
        groups[key]["items"].append({"name": entry.name, "is_dir": is_dir})

    series_list = sorted(groups.values(), key=lambda g: g["name"].casefold())
    total = sum(len(g["items"]) for g in series_list)
    return {"series": series_list, "total": total}


def apply_series_plan(relative_path: str) -> dict:
    """Group series seasons into per-series folders in the current directory."""
    plan = build_series_plan(relative_path)
    target_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)

    created: list[str] = []
    moved = 0
    errors: list[str] = []

    for group in plan["series"]:
        series_name = group["name"]
        existing = _find_existing_series_dir(target_dir, series_name)
        if existing:
            folder_name = existing
        else:
            try:
                create_directory(relative_path, series_name)
                folder_name = series_name
                created.append(series_name)
            except ValueError as exc:
                errors.append(f"{series_name} : {exc}")
                continue
        dest_rel = posixpath.join(relative_path, folder_name)
        for item in group["items"]:
            try:
                move_item(relative_path, item["name"], dest_rel)
                moved += 1
            except ValueError as exc:
                errors.append(f"{item['name']} : {exc}")

    clear_scandir_cache()
    return {
        "success": True,
        "created": created,
        "series_count": len(plan["series"]),
        "moved": moved,
        "errors": errors,
        "total": plan["total"],
    }
