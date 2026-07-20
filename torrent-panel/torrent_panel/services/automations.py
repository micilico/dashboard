"""Automation rule store – default rules, upsert, simulation."""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any

from ..models import AutomationRulePayload
from .media_automation import now_iso

logger = logging.getLogger("torrent_panel.automations")


class AutomationRuleStore:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._rules: list[dict[str, Any]] = []
        self._load()

    def _default_rules(self) -> list[dict[str, Any]]:
        generated_at = now_iso()
        return [
            {
                "id": secrets.token_hex(6),
                "name": "Pause si disque critique",
                "trigger": "disk_critical",
                "conditions": ["Stockage critique"],
                "actions": ["pause_downloads"],
                "enabled": False,
                "mode": "simulation",
                "createdAt": generated_at,
                "updatedAt": generated_at,
                "lastExecutedAt": None,
                "lastResult": "never",
            },
            {
                "id": secrets.token_hex(6),
                "name": "Alerte torrent bloqué",
                "trigger": "blocked_torrent",
                "conditions": ["Torrent bloqué plusieurs minutes"],
                "actions": ["notify_blocked_torrent"],
                "enabled": True,
                "mode": "simulation",
                "createdAt": generated_at,
                "updatedAt": generated_at,
                "lastExecutedAt": None,
                "lastResult": "never",
            },
            {
                "id": secrets.token_hex(6),
                "name": "Alerte backend indisponible",
                "trigger": "backend_unavailable",
                "conditions": ["Service distant indisponible"],
                "actions": ["notify_backend_unavailable"],
                "enabled": True,
                "mode": "simulation",
                "createdAt": generated_at,
                "updatedAt": generated_at,
                "lastExecutedAt": None,
                "lastResult": "never",
            },
        ]

    def _load(self) -> None:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._rules = self._default_rules()
            self._save()
            return
        except (OSError, ValueError):
            logger.warning("Unable to read automation rules state")
            self._rules = self._default_rules()
            return
        parsed = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        self._rules = parsed or self._default_rules()

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._state_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(self._rules, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self._state_path)
        except OSError:
            logger.warning("Unable to persist automation rules")

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._rules]

    def upsert(self, payload: AutomationRulePayload) -> dict[str, Any]:
        existing = next((item for item in self._rules if item.get("name") == payload.name), None)
        if existing is None:
            existing = {"id": secrets.token_hex(6), "createdAt": now_iso()}
            self._rules.append(existing)
        existing.update(
            {
                "name": payload.name,
                "trigger": payload.trigger,
                "conditions": payload.conditions,
                "actions": payload.actions,
                "enabled": payload.enabled,
                "mode": "simulation",
                "updatedAt": now_iso(),
                "lastExecutedAt": existing.get("lastExecutedAt"),
                "lastResult": existing.get("lastResult", "never"),
            }
        )
        self._save()
        return dict(existing)

    def simulate(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        alerts = snapshot.get("alerts", [])
        services = snapshot.get("services", [])
        for rule in self._rules:
            matched = False
            trigger = str(rule.get("trigger") or "")
            if trigger == "disk_critical":
                matched = any(item.get("service") == "Stockage" and item.get("severity") == "critical" for item in alerts)
            elif trigger == "blocked_torrent":
                matched = any(item.get("service") == "qBittorrent" for item in alerts)
            elif trigger == "backend_unavailable":
                matched = any(item.get("status") == "unavailable" for item in services)
            result = {
                "ruleId": rule.get("id"),
                "name": rule.get("name"),
                "mode": "simulation",
                "matched": matched,
                "actions": list(rule.get("actions") or []),
                "trigger": trigger,
                "date": now_iso(),
            }
            rule["lastExecutedAt"] = result["date"]
            rule["lastResult"] = "matched" if matched else "no_match"
            results.append(result)
        self._save()
        return results
