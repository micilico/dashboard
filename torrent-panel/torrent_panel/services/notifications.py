"""Notification center – dependency deduplication and acknowledgement."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from common import error_detail

from .media_automation import now_iso

logger = logging.getLogger("torrent_panel.notifications")


class NotificationCenter:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._records: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            logger.warning("Unable to read notification state")
            return
        if isinstance(payload, dict):
            self._records = {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._state_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(self._records, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self._state_path)
        except OSError:
            logger.warning("Unable to persist notification state")

    def reconcile(self, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen_codes: set[str] = set()
        for alert in alerts:
            code = str(alert.get("code") or "")
            if not code:
                continue
            seen_codes.add(code)
            record = self._records.get(code)
            if record is None:
                record = {
                    "code": code,
                    "service": alert.get("service", "Service"),
                    "severity": alert.get("severity", "warning"),
                    "message": alert.get("message", ""),
                    "firstSeenAt": alert.get("date") or now_iso(),
                    "lastSeenAt": alert.get("date") or now_iso(),
                    "occurrences": 1,
                    "status": "open",
                    "ackedAt": None,
                    "lastResult": "open",
                    "action": alert.get("action"),
                }
                self._records[code] = record
                continue
            record["service"] = alert.get("service", record.get("service"))
            record["severity"] = alert.get("severity", record.get("severity"))
            record["message"] = alert.get("message", record.get("message"))
            record["lastSeenAt"] = alert.get("date") or now_iso()
            record["occurrences"] = int(record.get("occurrences", 0) or 0) + 1
            record["action"] = alert.get("action")
            if record.get("status") == "acknowledged":
                record["status"] = "reopened"
                record["lastResult"] = "reopened"
        for code, record in self._records.items():
            if code not in seen_codes and record.get("status") == "open":
                record["lastResult"] = "stable"
        self._save()
        return self.snapshot()

    def snapshot(self) -> list[dict[str, Any]]:
        return sorted(
            [dict(item) for item in self._records.values()],
            key=lambda item: (item.get("status") == "acknowledged", item.get("lastSeenAt") or ""),
            reverse=True,
        )

    def acknowledge(self, code: str) -> dict[str, Any]:
        record = self._records.get(code)
        if not record:
            raise HTTPException(status_code=404, detail=error_detail("notification_not_found", "Alerte introuvable.", "Actualiser"))
        record["status"] = "acknowledged"
        record["ackedAt"] = now_iso()
        record["lastResult"] = "acknowledged"
        self._save()
        return dict(record)

    def reopen(self, code: str) -> dict[str, Any]:
        record = self._records.get(code)
        if not record:
            raise HTTPException(status_code=404, detail=error_detail("notification_not_found", "Alerte introuvable.", "Actualiser"))
        record["status"] = "reopened"
        record["ackedAt"] = None
        record["lastResult"] = "reopened"
        self._save()
        return dict(record)
