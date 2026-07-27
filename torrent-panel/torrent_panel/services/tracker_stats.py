"""Persistent tracker transfer statistics built from qBittorrent snapshots."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("torrent_panel.tracker_stats")


class TrackerStatsStore:
    def __init__(self, state_path: Path, *, history_days: int = 60) -> None:
        self._state_path = state_path
        self._history_days = history_days

    def observe(self, torrents: list[dict[str, Any]], tracker_index: dict[str, list[str]]) -> dict[str, Any]:
        state = self._load()
        today = datetime.now().astimezone().date().isoformat()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        previous = state.get("torrents") if isinstance(state.get("torrents"), dict) else {}
        days = state.get("days") if isinstance(state.get("days"), dict) else {}
        day = days.setdefault(today, {"trackers": {}})
        day_trackers = day.setdefault("trackers", {})

        current: dict[str, dict[str, Any]] = {}
        for torrent in torrents:
            torrent_hash = str(torrent.get("hash") or "").lower()
            if not torrent_hash:
                continue
            tracker = self._primary_tracker(torrent, tracker_index.get(torrent_hash, []))
            downloaded = self._positive_int(torrent.get("downloaded"))
            uploaded = self._positive_int(torrent.get("uploaded"))
            current[torrent_hash] = {
                "downloaded": downloaded,
                "uploaded": uploaded,
                "tracker": tracker,
            }
            old = previous.get(torrent_hash) if isinstance(previous.get(torrent_hash), dict) else None
            if not old:
                continue
            downloaded_delta = max(0, downloaded - self._positive_int(old.get("downloaded")))
            uploaded_delta = max(0, uploaded - self._positive_int(old.get("uploaded")))
            if downloaded_delta == 0 and uploaded_delta == 0:
                continue
            old_tracker = str(old.get("tracker") or tracker)
            bucket = day_trackers.setdefault(old_tracker, {"downloaded": 0, "uploaded": 0})
            bucket["downloaded"] = self._positive_int(bucket.get("downloaded")) + downloaded_delta
            bucket["uploaded"] = self._positive_int(bucket.get("uploaded")) + uploaded_delta

        state = {
            "updatedAt": now,
            "torrents": current,
            "days": self._trim_days(days),
        }
        self._save(state)
        return self.snapshot(state, torrents, tracker_index)

    def snapshot(
        self,
        state: dict[str, Any] | None = None,
        torrents: list[dict[str, Any]] | None = None,
        tracker_index: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        state = state or self._load()
        torrents = torrents or []
        tracker_index = tracker_index or {}
        days = state.get("days") if isinstance(state.get("days"), dict) else {}
        observed_trackers = self._observed_trackers(torrents, tracker_index)

        daily: list[dict[str, Any]] = []
        totals: dict[str, dict[str, Any]] = {
            tracker: {"tracker": tracker, "downloaded": 0, "uploaded": 0, "days": 0, "torrents": count}
            for tracker, count in observed_trackers.items()
        }
        for date_key in sorted(days):
            day = days.get(date_key) if isinstance(days.get(date_key), dict) else {}
            trackers = day.get("trackers") if isinstance(day.get("trackers"), dict) else {}
            for tracker, values in sorted(trackers.items()):
                if not isinstance(values, dict):
                    continue
                downloaded = self._positive_int(values.get("downloaded"))
                uploaded = self._positive_int(values.get("uploaded"))
                ratio = uploaded / downloaded if downloaded > 0 else 0
                daily.append(
                    {
                        "date": date_key,
                        "tracker": tracker,
                        "downloaded": downloaded,
                        "uploaded": uploaded,
                        "ratio": ratio,
                    }
                )
                total = totals.setdefault(
                    tracker,
                    {"tracker": tracker, "downloaded": 0, "uploaded": 0, "days": 0, "torrents": observed_trackers.get(tracker, 0)},
                )
                total["downloaded"] += downloaded
                total["uploaded"] += uploaded
                total["days"] += 1

        total_rows = []
        for row in totals.values():
            downloaded = self._positive_int(row.get("downloaded"))
            uploaded = self._positive_int(row.get("uploaded"))
            row["ratio"] = uploaded / downloaded if downloaded > 0 else 0
            total_rows.append(row)

        return {
            "updatedAt": state.get("updatedAt", ""),
            "observedDays": len(days),
            "totals": sorted(total_rows, key=lambda item: (item.get("downloaded", 0) + item.get("uploaded", 0)), reverse=True),
            "daily": daily[-200:],
        }

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"updatedAt": "", "torrents": {}, "days": {}}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("tracker stats state unreadable: %s", exc.__class__.__name__)
            return {"updatedAt": "", "torrents": {}, "days": {}}
        return payload if isinstance(payload, dict) else {"updatedAt": "", "torrents": {}, "days": {}}

    def _save(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_path.with_suffix(f"{self._state_path.suffix}.tmp")
        tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(self._state_path)

    def _trim_days(self, days: dict[str, Any]) -> dict[str, Any]:
        return {key: days[key] for key in sorted(days)[-self._history_days:]}

    def _observed_trackers(self, torrents: list[dict[str, Any]], tracker_index: dict[str, list[str]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for torrent in torrents:
            torrent_hash = str(torrent.get("hash") or "").lower()
            tracker = self._primary_tracker(torrent, tracker_index.get(torrent_hash, []))
            counts[tracker] = counts.get(tracker, 0) + 1
        return counts

    def _primary_tracker(self, torrent: dict[str, Any], domains: list[str]) -> str:
        tracker = str(torrent.get("tracker") or "").strip()
        if tracker:
            return tracker
        for domain in domains:
            if domain:
                return str(domain)
        return "Sans tracker"

    def _positive_int(self, value: Any) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0
