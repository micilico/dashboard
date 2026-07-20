"""Media automation manager – detects completed torrents and runs rclone/Jellyfin workflow."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

from ..config import (
    JELLYFIN_API_KEY,
    JELLYFIN_API_URL,
    JELLYFIN_LIBRARY_MAP,
    JELLYFIN_GLOBAL_FALLBACK,
    MEDIA_AUTOMATION_DEBOUNCE_SECONDS,
    MEDIA_AUTOMATION_ENABLED,
    MEDIA_AUTOMATION_HISTORY_LIMIT,
    MEDIA_AUTOMATION_JELLYFIN_DELAY_SECONDS,
    MEDIA_AUTOMATION_MAX_JELLYFIN_RETRIES,
    MEDIA_AUTOMATION_MAX_MOUNT_RETRIES,
    MEDIA_AUTOMATION_MAX_RCLONE_RETRIES,
    MEDIA_AUTOMATION_POLL_SECONDS,
    MEDIA_AUTOMATION_STATE_PATH,
    MEDIA_MOUNT_PATH,
    MONITOR_HTTP_TIMEOUT_SECONDS,
    PUBLIC_PREFIX,
    RCLONE_RC_REFRESH_DIR,
    RCLONE_RC_REFRESH_URL,
    RCLONE_REFRESH_MODE,
    RCLONE_SYSTEMD_RESTART_CMD,
)
from ..qbittorrent import QbitError, QBittorrentClient
from common import error_detail

logger = logging.getLogger("torrent_panel.media_automation")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sanitize_error_message(message: str) -> str:
    cleaned = re.sub(r"https?://\S+", "[url masquée]", str(message or ""))
    cleaned = re.sub(r"[A-Fa-f0-9]{32,}", "[identifiant masqué]", cleaned)
    return cleaned[:240] or "Erreur non détaillée."


def parse_category_library_map(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if separator and key.strip() and value.strip():
            mapping[key.strip().lower()] = value.strip()
    return mapping


@dataclass(frozen=True)
class MediaAutomationConfig:
    enabled: bool
    poll_seconds: float
    debounce_seconds: float
    jellyfin_delay_seconds: float
    max_rclone_retries: int
    max_mount_retries: int
    max_jellyfin_retries: int
    history_limit: int
    state_path: Path
    mount_path: str
    rclone_refresh_mode: str
    rclone_rc_refresh_url: str
    rclone_rc_refresh_dir: str
    rclone_systemd_unit: str
    rclone_systemd_restart_cmd: str
    jellyfin_api_url: str
    jellyfin_api_key: str
    jellyfin_library_map: dict[str, str]
    jellyfin_global_fallback: bool


def build_media_automation_config() -> MediaAutomationConfig:
    return MediaAutomationConfig(
        enabled=MEDIA_AUTOMATION_ENABLED,
        poll_seconds=max(2.0, MEDIA_AUTOMATION_POLL_SECONDS),
        debounce_seconds=max(5.0, MEDIA_AUTOMATION_DEBOUNCE_SECONDS),
        jellyfin_delay_seconds=max(0.0, MEDIA_AUTOMATION_JELLYFIN_DELAY_SECONDS),
        max_rclone_retries=max(1, MEDIA_AUTOMATION_MAX_RCLONE_RETRIES),
        max_mount_retries=max(1, MEDIA_AUTOMATION_MAX_MOUNT_RETRIES),
        max_jellyfin_retries=max(1, MEDIA_AUTOMATION_MAX_JELLYFIN_RETRIES),
        history_limit=max(10, MEDIA_AUTOMATION_HISTORY_LIMIT),
        state_path=MEDIA_AUTOMATION_STATE_PATH,
        mount_path=MEDIA_MOUNT_PATH,
        rclone_refresh_mode=RCLONE_REFRESH_MODE,
        rclone_rc_refresh_url=RCLONE_RC_REFRESH_URL,
        rclone_rc_refresh_dir=RCLONE_RC_REFRESH_DIR,
        rclone_systemd_unit=os.getenv("TORRENT_PANEL_RCLONE_SYSTEMD_UNIT", ""),
        rclone_systemd_restart_cmd=RCLONE_SYSTEMD_RESTART_CMD,
        jellyfin_api_url=JELLYFIN_API_URL.rstrip("/"),
        jellyfin_api_key=JELLYFIN_API_KEY,
        jellyfin_library_map=parse_category_library_map(JELLYFIN_LIBRARY_MAP),
        jellyfin_global_fallback=JELLYFIN_GLOBAL_FALLBACK,
    )


class MediaAutomationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.public_message = sanitize_error_message(message)


class MediaAutomationManager:
    def __init__(self, qbit: QBittorrentClient, config: MediaAutomationConfig) -> None:
        self._qbit = qbit
        self._config = config
        self._processed_hashes: dict[str, dict[str, Any]] = {}
        self._history: list[dict[str, Any]] = []
        self._known_completion: dict[str, bool] = {}
        self._pending_hashes: set[str] = set()
        self._pending_torrents: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._monitor_task: asyncio.Task[None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._bootstrapped = False
        self._latest_notification: dict[str, Any] | None = None

    async def start(self) -> None:
        self._load_state()
        if not self._config.enabled:
            return
        await self.bootstrap()
        self._monitor_task = asyncio.create_task(self._monitor_loop(), name="torrent-panel-media-monitor")
        self._worker_task = asyncio.create_task(self._worker_loop(), name="torrent-panel-media-worker")

    async def stop(self) -> None:
        for task in (self._monitor_task, self._worker_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._monitor_task = None
        self._worker_task = None
        self._save_state()

    async def bootstrap(self) -> None:
        if self._bootstrapped:
            return
        try:
            torrents = await self._qbit.torrents()
        except QbitError as exc:
            logger.warning("Media automation bootstrap skipped: %s", exc.code)
            return
        self.observe_torrents(torrents, allow_enqueue=False)
        self._bootstrapped = True

    def _load_state(self) -> None:
        try:
            payload = json.loads(self._config.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("Unable to read media automation state: %s", exc.__class__.__name__)
            return
        if isinstance(payload, dict):
            processed = payload.get("processed")
            history = payload.get("history")
            if isinstance(processed, dict):
                self._processed_hashes = {str(key): value for key, value in processed.items() if isinstance(value, dict)}
            if isinstance(history, list):
                self._history = [item for item in history if isinstance(item, dict)][-self._config.history_limit :]

    def _save_state(self) -> None:
        payload = {
            "processed": self._processed_hashes,
            "history": self._history[-self._config.history_limit :],
        }
        try:
            self._config.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._config.state_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self._config.state_path)
        except OSError as exc:
            logger.warning("Unable to persist media automation state: %s", exc.__class__.__name__)
            raise MediaAutomationError("La persistance de l'automatisation medias est indisponible.") from exc

    def observe_torrents(self, torrents: list[dict[str, Any]], *, allow_enqueue: bool) -> list[str]:
        completed_now: list[str] = []
        current_hashes = set()
        for torrent in torrents:
            torrent_hash = str(torrent.get("hash") or "").lower()
            if not torrent_hash:
                continue
            current_hashes.add(torrent_hash)
            completed = float(torrent.get("progress") or 0) >= 1 or int(torrent.get("completionOn") or 0) > 0
            previous = self._known_completion.get(torrent_hash)
            self._known_completion[torrent_hash] = completed
            if not allow_enqueue or previous is None:
                continue
            if completed and previous is False and torrent_hash not in self._processed_hashes:
                completed_now.append(torrent_hash)
                self._processed_hashes[torrent_hash] = {
                    "completedAt": now_iso(),
                    "name": str(torrent.get("name") or "Torrent"),
                }
                self._pending_hashes.add(torrent_hash)
                self._pending_torrents[torrent_hash] = dict(torrent)
        for stale_hash in list(self._known_completion.keys()):
            if stale_hash not in current_hashes:
                del self._known_completion[stale_hash]
        if completed_now:
            self._save_state()
            self._wake_event.set()
        return completed_now

    async def _monitor_loop(self) -> None:
        while True:
            try:
                torrents = await self._qbit.torrents()
                self.observe_torrents(torrents, allow_enqueue=True)
            except QbitError as exc:
                logger.warning("Media automation poll failed: %s", exc.code)
            await asyncio.sleep(self._config.poll_seconds)

    async def _worker_loop(self) -> None:
        while True:
            await self._wake_event.wait()
            self._wake_event.clear()
            await asyncio.sleep(self._config.debounce_seconds)
            await self.process_pending_batch()

    async def process_pending_batch(self) -> list[dict[str, Any]]:
        async with self._lock:
            hashes = list(self._pending_hashes)
            if not hashes:
                return []
            self._pending_hashes.clear()
            batch_torrents = [self._pending_torrents.pop(item) for item in hashes if item in self._pending_torrents]
            entries = [self._create_history_entry(torrent) for torrent in batch_torrents]
            await self._run_full_workflow(entries)
            self._save_state()
            return entries

    def _create_history_entry(self, torrent: dict[str, Any]) -> dict[str, Any]:
        completed_at = int(torrent.get("completionOn") or 0)
        entry = {
            "id": secrets.token_hex(8),
            "torrentHash": str(torrent.get("hash") or "").lower(),
            "torrentName": str(torrent.get("name") or "Torrent"),
            "category": str(torrent.get("category") or ""),
            "completedAt": datetime.fromtimestamp(completed_at, tz=UTC).isoformat() if completed_at > 0 else now_iso(),
            "state": "pending",
            "stateLabel": "En attente",
            "rclone": {"status": "pending", "result": "En attente"},
            "mount": {"status": "pending", "result": "En attente"},
            "jellyfin": {"status": "pending", "result": "En attente", "library": self._library_for_category(str(torrent.get("category") or ""))},
            "errorMessage": "",
            "updatedAt": now_iso(),
            "retry": {"full": False, "jellyfin": False},
        }
        self._history.insert(0, entry)
        del self._history[self._config.history_limit :]
        return entry

    def _set_notification(self, message: str, severity: str = "info", *, entry_id: str | None = None) -> None:
        self._latest_notification = {
            "message": message,
            "severity": severity,
            "entryId": entry_id,
            "date": now_iso(),
        }

    def _update_entry_state(self, entry: dict[str, Any], state: str, label: str) -> None:
        entry["state"] = state
        entry["stateLabel"] = label
        entry["updatedAt"] = now_iso()

    def _mark_step(self, entry: dict[str, Any], step: str, status: str, result: str, **extra: Any) -> None:
        entry[step] = {"status": status, "result": sanitize_error_message(result), **extra}
        entry["updatedAt"] = now_iso()

    def _library_for_category(self, category: str) -> str:
        return self._config.jellyfin_library_map.get(category.strip().lower(), "")

    async def _run_full_workflow(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        for entry in entries:
            self._update_entry_state(entry, "rclone_refresh", "Actualisation rclone")
            entry["retry"] = {"full": False, "jellyfin": False}
        self._set_notification("Téléchargement terminé — actualisation des médias…", entry_id=entries[0]["id"])
        rclone_ok = await self._attempt_rclone(entries)
        if not rclone_ok:
            return
        mount_ok = await self._attempt_mount(entries)
        if not mount_ok:
            return
        if self._config.jellyfin_delay_seconds:
            await asyncio.sleep(self._config.jellyfin_delay_seconds)
        await self._attempt_jellyfin(entries)

    async def _attempt_rclone(self, entries: list[dict[str, Any]]) -> bool:
        last_error = "Actualisation rclone impossible."
        for attempt in range(1, self._config.max_rclone_retries + 1):
            try:
                await self.refresh_rclone()
                for entry in entries:
                    self._mark_step(entry, "rclone", "success", "Actualisation rclone effectuée.", attempts=attempt)
                    self._update_entry_state(entry, "mount_wait", "Attente du montage")
                return True
            except MediaAutomationError as exc:
                last_error = exc.public_message
                await asyncio.sleep(min(20, attempt * 2))
        for entry in entries:
            self._mark_step(entry, "rclone", "failed", last_error, attempts=self._config.max_rclone_retries)
            self._mark_step(entry, "mount", "skipped", "Montage non vérifié.")
            self._mark_step(entry, "jellyfin", "skipped", "Scan Jellyfin non lancé.")
            entry["errorMessage"] = last_error
            entry["retry"] = {"full": True, "jellyfin": False}
            self._update_entry_state(entry, "failed", "Échec définitif")
        self._set_notification("Actualisation impossible — intervention requise", "critical", entry_id=entries[0]["id"])
        return False

    async def _attempt_mount(self, entries: list[dict[str, Any]]) -> bool:
        last_error = "Montage indisponible."
        for attempt in range(1, self._config.max_mount_retries + 1):
            try:
                await self.wait_for_mount()
                for entry in entries:
                    self._mark_step(entry, "mount", "success", "Montage disponible.", attempts=attempt)
                    self._update_entry_state(entry, "jellyfin_requested", "Scan Jellyfin demandé")
                self._set_notification("Montage disponible — scan Jellyfin lancé", entry_id=entries[0]["id"])
                return True
            except MediaAutomationError as exc:
                last_error = exc.public_message
                await asyncio.sleep(min(30, attempt * 3))
        for entry in entries:
            self._mark_step(entry, "mount", "failed", last_error, attempts=self._config.max_mount_retries)
            self._mark_step(entry, "jellyfin", "skipped", "Scan Jellyfin non lancé.")
            entry["errorMessage"] = last_error
            entry["retry"] = {"full": True, "jellyfin": False}
            self._update_entry_state(entry, "failed", "Échec définitif")
        self._set_notification("Actualisation impossible — intervention requise", "critical", entry_id=entries[0]["id"])
        return False

    async def _attempt_jellyfin(self, entries: list[dict[str, Any]]) -> bool:
        last_error = "Scan Jellyfin impossible."
        for attempt in range(1, self._config.max_jellyfin_retries + 1):
            try:
                targets = sorted({entry["jellyfin"]["library"] for entry in entries if entry["jellyfin"]["library"]})
                scan_result = await self.trigger_jellyfin_scan(targets)
                for entry in entries:
                    library = entry["jellyfin"]["library"] or ("global" if scan_result["scope"] == "global" else "")
                    self._mark_step(
                        entry,
                        "jellyfin",
                        "success",
                        "Scan Jellyfin lancé.",
                        attempts=attempt,
                        library=library,
                        scope=scan_result["scope"],
                    )
                    entry["retry"] = {"full": False, "jellyfin": False}
                    self._update_entry_state(entry, "completed", "Terminé")
                self._set_notification("Bibliothèque Jellyfin actualisée", entry_id=entries[0]["id"])
                return True
            except MediaAutomationError as exc:
                last_error = exc.public_message
                await asyncio.sleep(min(20, attempt * 2))
        for entry in entries:
            self._mark_step(entry, "jellyfin", "failed", last_error, attempts=self._config.max_jellyfin_retries, library=entry["jellyfin"]["library"])
            entry["errorMessage"] = last_error
            entry["retry"] = {"full": False, "jellyfin": True}
            self._update_entry_state(entry, "partial_failure", "Échec partiel")
        self._set_notification("Actualisation impossible — intervention requise", "warning", entry_id=entries[0]["id"])
        return False

    async def refresh_rclone(self) -> None:
        mode = self._config.rclone_refresh_mode
        if mode not in {"auto", "rc", "systemd"}:
            raise MediaAutomationError("Mode de rafraîchissement rclone invalide.")
        if mode in {"auto", "rc"}:
            try:
                await self._refresh_rclone_rc()
                return
            except MediaAutomationError:
                if mode == "rc":
                    raise
        if mode in {"auto", "systemd"}:
            await self._refresh_rclone_systemd()
            return
        raise MediaAutomationError("Aucune méthode rclone disponible.")

    async def _refresh_rclone_rc(self) -> None:
        if not self._config.rclone_rc_refresh_url:
            raise MediaAutomationError("Endpoint RC rclone non configuré.")
        params: dict[str, str] = {}
        if self._config.rclone_rc_refresh_dir:
            params["dir"] = self._config.rclone_rc_refresh_dir
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS)) as client:
                response = await client.post(self._config.rclone_rc_refresh_url, params=params)
        except httpx.HTTPError as exc:
            raise MediaAutomationError("Endpoint RC rclone inaccessible.") from exc
        if response.status_code >= 400:
            raise MediaAutomationError(f"Rclone RC a refusé l'actualisation ({response.status_code}).")

    async def _refresh_rclone_systemd(self) -> None:
        raw_cmd = self._config.rclone_systemd_restart_cmd.strip()
        if not raw_cmd:
            raise MediaAutomationError("Commande systemd rclone non configurée.")
        command = [part for part in raw_cmd.split() if part]
        if command[:2] != ["systemctl", "restart"] or len(command) != 3:
            raise MediaAutomationError("Commande systemd rclone refusee.")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        except TimeoutError as exc:
            process.kill()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
            raise MediaAutomationError("Commande systemd rclone expiree.") from exc
        if len(stdout) > 4096 or len(stderr) > 4096:
            raise MediaAutomationError("Sortie systemd rclone excessive.")
        if process.returncode != 0:
            raise MediaAutomationError("Commande systemd rclone refusee.")

    async def wait_for_mount(self) -> None:
        mount_path = Path(self._config.mount_path)
        if not mount_path.exists():
            raise MediaAutomationError("Le chemin du montage est introuvable.")
        try:
            if mount_path.is_dir():
                next(mount_path.iterdir(), None)
            else:
                mount_path.stat()
        except OSError as exc:
            raise MediaAutomationError("Le montage ne répond pas encore.") from exc

    async def _jellyfin_post(self, client: httpx.AsyncClient, path: str) -> httpx.Response:
        auth_attempts = [
            {"headers": {"X-Emby-Token": self._config.jellyfin_api_key}, "params": None},
            {"headers": None, "params": {"api_key": self._config.jellyfin_api_key}},
            {"headers": {"Authorization": f'MediaBrowser Token="{self._config.jellyfin_api_key}"'}, "params": None},
        ]
        last_response: httpx.Response | None = None
        for attempt in auth_attempts:
            response = await client.post(
                f"{self._config.jellyfin_api_url}{path}",
                headers=attempt["headers"],
                params=attempt["params"],
            )
            last_response = response
            if response.status_code != 401:
                return response
        return last_response if last_response is not None else httpx.Response(status_code=500)

    async def trigger_jellyfin_scan(self, library_ids: list[str]) -> dict[str, str]:
        if not self._config.jellyfin_api_key:
            raise MediaAutomationError("Clé API Jellyfin absente côté backend.")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS),
                trust_env=False,
            ) as client:
                if library_ids:
                    for library_id in library_ids:
                        response = await self._jellyfin_post(client, f"/Items/{library_id}/Refresh")
                        if response.status_code >= 400:
                            raise MediaAutomationError(f"Bibliothèque Jellyfin refusée ({response.status_code}).")
                    return {"scope": "targeted"}
                if not self._config.jellyfin_global_fallback:
                    raise MediaAutomationError("Aucune bibliothèque Jellyfin mappée.")
                response = await self._jellyfin_post(client, "/Library/Refresh")
                if response.status_code >= 400:
                    raise MediaAutomationError(f"Jellyfin a refusé le scan ({response.status_code}).")
                return {"scope": "global"}
        except httpx.HTTPError as exc:
            raise MediaAutomationError("API Jellyfin indisponible.") from exc

    def snapshot(self) -> dict[str, Any]:
        entries = [dict(item) for item in self._history[: min(len(self._history), self._config.history_limit)]]
        return {
            "enabled": self._config.enabled,
            "entries": entries,
            "notification": dict(self._latest_notification) if self._latest_notification else None,
        }

    def dashboard_alerts(self) -> list[dict[str, Any]]:
        from .monitoring import build_alert
        
        alerts: list[dict[str, Any]] = []
        for entry in self._history[:5]:
            if entry["state"] in {"failed", "partial_failure"}:
                alerts.append(
                    build_alert(
                        "critical" if entry["state"] == "failed" else "warning",
                        "Automatisation médias",
                        f"{entry['torrentName']} : {entry['errorMessage'] or entry['stateLabel']}",
                        action={"kind": "open", "label": "Afficher", "url": f"{PUBLIC_PREFIX or ''}/?view=home#mediaAutomation"},
                        code=f"media_{entry['id']}",
                    )
                )
        return alerts

    async def retry(self, entry_id: str, scope: str) -> dict[str, Any]:
        async with self._lock:
            entry = next((item for item in self._history if item.get("id") == entry_id), None)
            if not entry:
                raise HTTPException(status_code=404, detail=error_detail("workflow_not_found", "Historique introuvable.", "Actualiser"))
            if scope not in {"full", "jellyfin"}:
                raise HTTPException(status_code=422, detail=error_detail("workflow_retry_invalid", "Relance invalide.", "Réessayer"))
            if scope == "jellyfin" and not entry.get("retry", {}).get("jellyfin"):
                raise HTTPException(status_code=409, detail=error_detail("workflow_retry_forbidden", "Scan Jellyfin non relançable seul.", "Réessayer"))
            if scope == "full" and not entry.get("retry", {}).get("full"):
                raise HTTPException(status_code=409, detail=error_detail("workflow_retry_forbidden", "Workflow complet non relançable.", "Réessayer"))
            entry["errorMessage"] = ""
            if scope == "full":
                entry["rclone"] = {"status": "pending", "result": "Nouvelle tentative demandée."}
                entry["mount"] = {"status": "pending", "result": "En attente"}
                entry["jellyfin"] = {"status": "pending", "result": "En attente", "library": entry.get("jellyfin", {}).get("library", "")}
                await self._run_full_workflow([entry])
            else:
                self._update_entry_state(entry, "jellyfin_requested", "Scan Jellyfin demandé")
                await self._attempt_jellyfin([entry])
            self._save_state()
            return dict(entry)

    async def manual_action(self, action: str) -> dict[str, str]:
        async with self._lock:
            if action == "rclone-refresh":
                await self.refresh_rclone()
                self._set_notification("Actualisation rclone lancée manuellement.")
                self._save_state()
                return {"status": "ok", "message": "Actualisation rclone lancée."}
            if action == "jellyfin-refresh":
                await self.trigger_jellyfin_scan([])
                self._set_notification("Scan Jellyfin lancé manuellement.")
                self._save_state()
                return {"status": "ok", "message": "Scan Jellyfin lancé."}
            raise HTTPException(
                status_code=404,
                detail=error_detail("manual_action_not_found", "Action manuelle inconnue.", "Actualiser"),
            )
