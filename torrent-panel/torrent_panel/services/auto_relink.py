"""Auto-relink manager – periodically reattaches torrents stuck in missingFiles."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from ..qbittorrent import QbitError
from .media_automation import now_iso
from .relink import apply_relink_plan, build_relink_plan

logger = logging.getLogger("torrent_panel.auto_relink")


class AutoRelinkManager:
    def __init__(
        self,
        qbit: Any,
        notifications: Any,
        *,
        enabled: bool,
        interval_seconds: float,
    ) -> None:
        self._qbit = qbit
        self._notifications = notifications
        self._enabled = enabled
        self._interval = max(60.0, float(interval_seconds))
        self._task: asyncio.Task[None] | None = None
        self._last_run_at: str | None = None
        self._last_result: dict[str, Any] | None = None

    async def start(self) -> None:
        if not self._enabled:
            return
        self._task = asyncio.create_task(self._loop(), name="torrent-panel-auto-relink")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def run_once(self) -> dict[str, Any] | None:
        try:
            plan = await build_relink_plan(self._qbit)
        except QbitError as exc:
            logger.warning("Auto-relink skipped: %s", exc.code)
            return None
        if not plan["relink"]:
            self._last_run_at = now_iso()
            self._last_result = {"relinked": 0, "failed": 0, "skipped": plan["skippedCount"]}
            return None
        result = await apply_relink_plan(self._qbit, plan)
        logger.info(
            "Auto-relink: %d torrent(s) relinké(s), %d échec(s), %d sans catégorie.",
            result["relinked"],
            result["failed"],
            plan["skippedCount"],
        )
        self._notify(plan, result)
        self._last_run_at = now_iso()
        self._last_result = dict(result)
        return result

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("Auto-relink loop crashed")
            await asyncio.sleep(self._interval)

    def _notify(self, plan: dict[str, Any], result: dict[str, Any]) -> None:
        from .monitoring import build_alert

        alerts: list[dict[str, Any]] = []
        if result["relinked"]:
            locations = ", ".join(f"{detail['count']} → {detail['location']}" for detail in result["details"] if detail.get("ok"))
            alerts.append(
                build_alert(
                    "info",
                    "Réparation automatique",
                    f"{result['relinked']} torrent(s) relinké(s) automatiquement ({locations}).",
                    code="auto_relink_done",
                )
            )
        if result["failed"] or result["recheckFailed"]:
            alerts.append(
                build_alert(
                    "warning",
                    "Réparation automatique",
                    f"{result['failed']} torrent(s) non relinké(s), {result['recheckFailed']} revérification(s) échouée(s).",
                    code="auto_relink_failed",
                )
            )
        if plan.get("skippedCount"):
            alerts.append(
                build_alert(
                    "warning",
                    "Réparation automatique",
                    f"{plan['skippedCount']} torrent(s) manquant(s) sans catégorie ou chemin — intervention manuelle requise.",
                    code="auto_relink_skipped",
                )
            )
        if alerts:
            self._notifications.reconcile(alerts)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "intervalSeconds": self._interval,
            "lastRunAt": self._last_run_at,
            "lastResult": dict(self._last_result) if self._last_result else None,
        }
