"""Small persistent journal for library organization runs.

The journal is deliberately boring: it stores sanitized, user-facing facts
needed to restore a layout manually, never credentials or service URLs.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _public_path(value: Any) -> str:
    """Keep paths relative; absolute host paths are never journaled."""
    path = str(value or "").replace("\\", "/").strip()
    return path.lstrip("/") if path else ""


class OrganizationRunStore:
    def __init__(self, path: Path, max_runs: int = 100) -> None:
        self.path = path
        self.max_runs = max(1, max_runs)

    def _read(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return payload if isinstance(payload, list) else []

    def _write(self, runs: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(runs[: self.max_runs], ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)

    def create(self, requested_hashes: list[str]) -> dict[str, Any]:
        run = {
            "runId": secrets.token_urlsafe(12),
            "createdAt": _timestamp(),
            "updatedAt": _timestamp(),
            "status": "preview",
            "requestedHashes": [str(value).lower()[:64] for value in requested_hashes],
            "operations": [],
            "warnings": [],
        }
        runs = self._read()
        runs.insert(0, run)
        self._write(runs)
        return run

    def update(self, run_id: str, **changes: Any) -> dict[str, Any] | None:
        runs = self._read()
        for run in runs:
            if run.get("runId") != run_id:
                continue
            run.update(changes)
            run["updatedAt"] = _timestamp()
            self._write(runs)
            return run
        return None

    def get(self, run_id: str) -> dict[str, Any] | None:
        return next((run for run in self._read() if run.get("runId") == run_id), None)


def journal_operation(entry: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Build one sanitized journal item from a plan entry and its result."""
    return {
        "hash": str(entry.get("hash") or "").lower()[:64],
        "name": str(entry.get("name") or "Torrent")[:200],
        "oldPaths": [
            {"path": _public_path(item.get("oldPath")), "newPath": _public_path(item.get("newPath"))}
            for item in entry.get("operations", [])
            if isinstance(item, dict)
        ],
        "oldLocation": _public_path(entry.get("currentPath")),
        "newLocation": _public_path(entry.get("targetPath")),
        "initialState": str(entry.get("initialState") or "")[:40],
        "method": "qBittorrent metadata rename + setLocation + recheck",
        "verification": "requested" if result.get("success") else "not_requested",
        "success": bool(result.get("success")),
        "error": str(result.get("error") or "")[:500],
        "warnings": [str(value)[:300] for value in result.get("warnings", []) if value],
        "recordedAt": _timestamp(),
    }


def journal_orphan_operation(orphan: dict[str, Any], operations: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "hash": None,
        "name": str(orphan.get("path") or "Média orphelin")[:300],
        "oldPaths": [
            {
                "path": _public_path(item.get("path")),
                "newPath": _public_path(item.get("dest")),
                "operation": str(item.get("op") or "")[:20],
            }
            for item in operations
        ],
        "oldLocation": _public_path(orphan.get("path")),
        "newLocation": _public_path(next((item.get("dest") for item in reversed(operations) if item.get("dest")), "")),
        "initialState": "not_applicable",
        "method": "Cloud Panel move-only transaction",
        "verification": "not_applicable",
        "success": bool(result.get("success")),
        "error": str(result.get("error") or "")[:500],
        "warnings": [],
        "recordedAt": _timestamp(),
    }


def journal_duplicate_operation(group: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Store a sanitized, manual-restoration-friendly duplicate merge record."""
    return {
        "type": "duplicate_merge",
        "canonicalPath": _public_path(group.get("canonicalPath")),
        "sourcePaths": [_public_path(value) for value in group.get("sourcePaths", [])],
        "movedFiles": [_public_path(item.get("sourcePath")) for item in group.get("files", []) if item.get("decision") == "move"],
        "trashedFiles": [_public_path(item.get("sourcePath")) for item in group.get("files", []) if item.get("decision") == "reuse"],
        "torrentChanges": [str(item.get("hash") or "")[:64] for item in group.get("associatedTorrents", [])],
        "verification": "started" if result.get("success") else "not_started",
        "rollback": "not_required" if result.get("success") else "manual_review",
        "success": bool(result.get("success")),
        "error": str(result.get("error") or "")[:500],
        "recordedAt": _timestamp(),
    }
