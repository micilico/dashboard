"""Ratio monitor – flags torrents whose upload/download ratio exceeds a threshold.

The threshold is editable from the interface and persisted in a state file so it
survives restarts. The environment variable provides only the initial value.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("torrent_panel.ratio_monitor")

MIN_THRESHOLD = 1.0
MAX_THRESHOLD = 100.0
_STEP = 0.5


class RatioThresholdError(ValueError):
    pass


class RatioMonitor:
    def __init__(self, state_path: Path, *, threshold: float = 10.0) -> None:
        self._state_path = state_path
        self._threshold = float(threshold)
        self._load()

    @property
    def threshold(self) -> float:
        return self._threshold

    def settings(self) -> dict[str, Any]:
        return {
            "threshold": self._threshold,
            "minThreshold": MIN_THRESHOLD,
            "maxThreshold": MAX_THRESHOLD,
            "step": _STEP,
        }

    def set_threshold(self, value: float) -> dict[str, Any]:
        try:
            validated = round(float(value) / _STEP) * _STEP
        except (TypeError, ValueError) as exc:
            raise RatioThresholdError("Valeur de seuil invalide.") from exc
        if not MIN_THRESHOLD <= validated <= MAX_THRESHOLD:
            raise RatioThresholdError("Le seuil doit être compris entre 1 et 100.")
        self._threshold = validated
        self._save()
        return self.settings()

    def evaluate(self, torrents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for torrent in torrents:
            uploaded = self._positive_int(torrent.get("uploaded"))
            downloaded = self._positive_int(torrent.get("downloaded"))
            if downloaded <= 0 or uploaded <= 0:
                continue
            ratio = uploaded / downloaded
            if ratio > self._threshold:
                findings.append(
                    {
                        "hash": str(torrent.get("hash") or "").lower(),
                        "name": str(torrent.get("name") or "Torrent sans nom"),
                        "ratio": round(ratio, 2),
                        "downloaded": downloaded,
                        "uploaded": uploaded,
                        "tracker": str(torrent.get("tracker") or ""),
                    }
                )
        findings.sort(key=lambda item: item["ratio"], reverse=True)
        return findings

    def build_alerts(self, torrents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        findings = self.evaluate(torrents)
        alerts: list[dict[str, Any]] = []
        for finding in findings:
            alerts.append(self._build_alert(finding))
        return alerts

    def _build_alert(self, finding: dict[str, Any]) -> dict[str, Any]:
        from ..services.monitoring import build_alert
        from ..config import PUBLIC_PREFIX

        return build_alert(
            "warning",
            "Ratio UP/DL",
            f"Ratio élevé ({finding['ratio']}) pour « {finding['name']} » (seuil {self._threshold}).",
            action={"kind": "open", "label": "Afficher", "url": f"{PUBLIC_PREFIX or ''}/?view=torrents"},
            code=f"ratio_high_{finding['hash'] or 'unknown'}",
        )

    def _load(self) -> None:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        try:
            self._threshold = float(payload.get("threshold"))
        except (TypeError, ValueError):
            pass

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_path.with_suffix(f"{self._state_path.suffix}.tmp")
            tmp_path.write_text(
                json.dumps(
                    {"threshold": self._threshold, "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds")},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(self._state_path)
        except OSError:
            logger.warning("Unable to persist ratio monitor state")

    def _positive_int(self, value: Any) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0
