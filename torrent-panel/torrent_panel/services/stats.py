"""Persistent daily statistics store for the torrent panel.

Records one consolidated row per day: exchanged volumes (deltas computed from
qBittorrent counters), disk usage, torrent activity and derived counters.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("torrent_panel.stats")


class StatsStore:
    def __init__(self, state_path: Path, *, history_days: int = 60) -> None:
        self._state_path = state_path
        self._history_days = max(7, int(history_days))

    def observe(
        self,
        torrents: list[dict[str, Any]],
        *,
        disk: dict[str, Any] | None = None,
        media_history: list[dict[str, Any]] | None = None,
        alerts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        state = self._load()
        today = datetime.now().astimezone().date().isoformat()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        previous = state.get("torrents") if isinstance(state.get("torrents"), dict) else {}
        days = state.get("days") if isinstance(state.get("days"), dict) else {}
        day = days.setdefault(today, {})
        day.setdefault("downloaded", 0)
        day.setdefault("uploaded", 0)

        current: dict[str, dict[str, Any]] = {}
        downloaded_delta = 0
        uploaded_delta = 0
        speed_sum = 0.0
        for torrent in torrents:
            torrent_hash = str(torrent.get("hash") or "").lower()
            if not torrent_hash:
                continue
            downloaded = self._positive_int(torrent.get("downloaded"))
            uploaded = self._positive_int(torrent.get("uploaded"))
            current[torrent_hash] = {
                "downloaded": downloaded,
                "uploaded": uploaded,
            }
            old = previous.get(torrent_hash) if isinstance(previous.get(torrent_hash), dict) else None
            if old:
                downloaded_delta += max(0, downloaded - self._positive_int(old.get("downloaded")))
                uploaded_delta += max(0, uploaded - self._positive_int(old.get("uploaded")))
            speed_sum += self._positive_float(torrent.get("downloadSpeed")) + self._positive_float(torrent.get("uploadSpeed"))

        day["downloaded"] = self._positive_int(day.get("downloaded")) + downloaded_delta
        day["uploaded"] = self._positive_int(day.get("uploaded")) + uploaded_delta
        day["ratio"] = round(day["uploaded"] / day["downloaded"], 2) if day["downloaded"] else 0.0

        day["downloadingTorrents"] = len(
            [item for item in torrents if str(item.get("state") or "") in {"downloading", "forcedDL", "metaDL"}]
        )
        day["activeTorrents"] = len(
            [
                item
                for item in torrents
                if str(item.get("state") or "")
                in {
                    "downloading",
                    "forcedDL",
                    "metaDL",
                    "uploading",
                    "forcedUP",
                    "stalledDL",
                    "stalledUP",
                    "checkingDL",
                    "checkingUP",
                    "checkingResumeData",
                    "queuedDL",
                    "queuedUP",
                }
            ]
        )
        day["blockedTorrents"] = len(
            [item for item in torrents if str(item.get("state") or "") in {"error", "missingFiles"}]
        )
        day["avgSpeedBytes"] = round(speed_sum / len(torrents)) if torrents else 0

        if isinstance(disk, dict):
            day["diskUsedPercent"] = round(float(disk.get("usedPercent") or 0), 1)
            day["diskFreeBytes"] = self._positive_int(disk.get("freeBytes"))
            day["diskTotalBytes"] = self._positive_int(disk.get("totalBytes"))

        day["mediaCompleted"] = len(
            [
                entry
                for entry in (media_history or [])
                if str(entry.get("completedAt") or entry.get("updatedAt") or "").startswith(today)
            ]
        )
        day["alerts"] = len(alerts or [])

        state = {
            "updatedAt": now,
            "torrents": current,
            "days": self._trim_days(days),
        }
        self._save(state)
        return self.snapshot(state)

    def snapshot(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = state or self._load()
        days = state.get("days") if isinstance(state.get("days"), dict) else {}
        daily: list[dict[str, Any]] = []
        total_downloaded = 0
        total_uploaded = 0
        for date_key in sorted(days):
            day = days.get(date_key) if isinstance(days.get(date_key), dict) else {}
            downloaded = self._positive_int(day.get("downloaded"))
            uploaded = self._positive_int(day.get("uploaded"))
            total_downloaded += downloaded
            total_uploaded += uploaded
            daily.append(
                {
                    "date": date_key,
                    "downloaded": downloaded,
                    "uploaded": uploaded,
                    "ratio": round(day.get("ratio", 0) or 0, 2),
                    "diskUsedPercent": day.get("diskUsedPercent"),
                    "diskFreeBytes": self._positive_int(day.get("diskFreeBytes")),
                    "diskTotalBytes": self._positive_int(day.get("diskTotalBytes")),
                    "activeTorrents": self._positive_int(day.get("activeTorrents")),
                    "downloadingTorrents": self._positive_int(day.get("downloadingTorrents")),
                    "blockedTorrents": self._positive_int(day.get("blockedTorrents")),
                    "avgSpeedBytes": self._positive_int(day.get("avgSpeedBytes")),
                    "mediaCompleted": self._positive_int(day.get("mediaCompleted")),
                    "alerts": self._positive_int(day.get("alerts")),
                }
            )
        return {
            "updatedAt": state.get("updatedAt", ""),
            "totals": {
                "downloaded": total_downloaded,
                "uploaded": total_uploaded,
                "ratio": round(total_uploaded / total_downloaded, 2) if total_downloaded else 0.0,
                "observedDays": len(days),
            },
            "daily": daily[-self._history_days:],
        }

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"updatedAt": "", "torrents": {}, "days": {}}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("stats state unreadable: %s", exc.__class__.__name__)
            return {"updatedAt": "", "torrents": {}, "days": {}}
        return payload if isinstance(payload, dict) else {"updatedAt": "", "torrents": {}, "days": {}}

    def _save(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_path.with_suffix(f"{self._state_path.suffix}.tmp")
        tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(self._state_path)

    def _trim_days(self, days: dict[str, Any]) -> dict[str, Any]:
        return {key: days[key] for key in sorted(days)[-self._history_days:]}

    def _positive_int(self, value: Any) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0

    def _positive_float(self, value: Any) -> float:
        try:
            return max(0.0, float(value or 0))
        except (TypeError, ValueError):
            return 0.0
