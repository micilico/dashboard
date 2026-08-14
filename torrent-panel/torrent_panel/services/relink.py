"""Relink service – reattach torrents whose data was moved outside their save path.

A torrent lands in the ``missingFiles`` state when its files were relocated
(e.g. category-driven automatic torrent management) while qBittorrent still
points at the old save path. The repair is: set the torrent's save path to the
save path of its category (via ``setLocation``) and force a recheck.
"""

from __future__ import annotations

import os
import posixpath
from typing import Any

from ..qbittorrent import QbitError
from .media_automation import now_iso

MISSING_STATES = {"missingFiles", "error"}


def _normalize_path(value: str) -> str:
    return posixpath.normpath(str(value or "").strip().replace(os.sep, "/")).rstrip("/")


def _category_save_path(categories: dict[str, dict[str, Any]], category: str) -> str:
    for key, item in categories.items():
        if key == category or key.strip().lower() == category.strip().lower():
            return _normalize_path(str(item.get("savePath") or ""))
    return ""


def _skipped_entry(torrent: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "hash": str(torrent.get("hash") or "").lower(),
        "name": str(torrent.get("name") or "Torrent"),
        "category": str(torrent.get("category") or ""),
        "reason": reason,
    }


def build_relink_plan(
    torrents: list[dict[str, Any]],
    categories: dict[str, dict[str, Any]],
    *,
    hashes: list[str] | None = None,
) -> dict[str, Any]:
    """Build the repair plan without mutating qBittorrent."""
    selected_hashes = {str(item).lower() for item in (hashes or [])} if hashes else None
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
        if selected_hashes is None and str(torrent.get("state") or "") not in MISSING_STATES:
            continue

        included += 1
        category = str(torrent.get("category") or "").strip()
        target = _category_save_path(categories, category) if category else ""
        current = _normalize_path(str(torrent.get("savePath") or torrent.get("save_path") or ""))

        if not category:
            skipped.append(_skipped_entry(torrent, "Sans catégorie"))
            continue
        if not target:
            skipped.append(_skipped_entry(torrent, "Catégorie sans chemin configuré"))
            continue

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
        "generatedAt": now_iso(),
    }


async def apply_relink_plan(qbit: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Apply a plan: set location per group, then force recheck. Never raises."""
    failures: list[dict[str, Any]] = []
    relinked = 0
    details: list[dict[str, Any]] = []

    for group in plan.get("relink", []):
        try:
            await qbit.set_location_many(list(group["hashes"]), group["location"])
            relinked += len(group["hashes"])
            details.append({"location": group["location"], "count": len(group["hashes"]), "ok": True})
        except QbitError as exc:
            details.append({"location": group["location"], "count": len(group["hashes"]), "ok": False, "error": exc.public_message})
            failures.extend(
                {
                    "hash": torrent_hash,
                    "name": name,
                    "message": exc.public_message,
                }
                for torrent_hash, name in zip(group["hashes"], group["names"])
            )

    recheck_hashes = [entry["hash"] for group in plan.get("relink", []) for entry in group["entries"]]
    recheck_hashes.extend(entry["hash"] for entry in plan.get("recheckOnly", []))
    recheck_hashes = list(dict.fromkeys(recheck_hashes))
    failed_rechecks = 0
    if recheck_hashes:
        try:
            await qbit.recheck_many(recheck_hashes)
        except QbitError:
            failed_rechecks = len(recheck_hashes)

    return {
        "relinked": relinked,
        "rechecked": len(recheck_hashes),
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
    """Build the plan and optionally apply it to the missing torrents."""
    torrents = await qbit.torrents()
    categories = await qbit.categories()
    plan = build_relink_plan(torrents, categories, hashes=hashes)
    if preview or not plan["relink"]:
        return {"plan": plan, "result": None}
    result = await apply_relink_plan(qbit, plan)
    return {"plan": plan, "result": result}
