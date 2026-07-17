import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .qbittorrent import QBittorrentClient, QbitConfig, QbitError

logging.basicConfig(level=os.getenv("TORRENT_PANEL_LOG_LEVEL", "INFO"))
logger = logging.getLogger("torrent_panel")

STATIC_DIR = Path(__file__).parent / "static"
HASH_RE = re.compile(r"^[A-Fa-f0-9]{40}([A-Fa-f0-9]{24})?$")
PUBLIC_PREFIX = os.getenv("TORRENT_PANEL_PUBLIC_PREFIX", "/torrent-panel").rstrip("/")
PROWLARR_PANEL_PUBLIC_PREFIX = os.getenv("PROWLARR_PANEL_PUBLIC_PREFIX", "/prowlarr-panel").rstrip("/")
CSRF_COOKIE = "torrent_panel_csrf"
CSRF_HEADER = "X-Torrent-Panel-CSRF"
INTERNAL_AUTH_HEADER = "X-Dashboard-Internal-Auth"
INTERNAL_AUTH_SECRET = os.getenv("TORRENT_PANEL_INTERNAL_AUTH_SECRET", "")
PROWLARR_PANEL_INTERNAL_AUTH_SECRET = os.getenv("PROWLARR_PANEL_INTERNAL_AUTH_SECRET", "")
MAX_RATE_KEYS = int(os.getenv("TORRENT_PANEL_RATE_LIMIT_KEYS", "2048"))
RATE_LIMIT_CALLS = int(os.getenv("TORRENT_PANEL_RATE_LIMIT_CALLS", "40"))
RATE_LIMIT_SECONDS = int(os.getenv("TORRENT_PANEL_RATE_LIMIT_SECONDS", "60"))
CSRF_TOKEN_TTL_SECONDS = int(os.getenv("TORRENT_PANEL_CSRF_TOKEN_TTL_SECONDS", "43200"))
MAX_CSRF_TOKENS = int(os.getenv("TORRENT_PANEL_CSRF_TOKEN_KEYS", "128"))
TRUSTED_PROXY_IPS = {
    item.strip()
    for item in os.getenv("TORRENT_PANEL_TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if item.strip()
}
ALLOWED_SAVE_PATHS = {
    item.strip()
    for item in os.getenv("TORRENT_PANEL_ALLOWED_SAVE_PATHS", "").split(",")
    if item.strip()
}
MONITOR_HTTP_TIMEOUT_SECONDS = float(os.getenv("TORRENT_PANEL_MONITOR_HTTP_TIMEOUT_SECONDS", "4"))
MONITOR_DISK_PATH = os.getenv("TORRENT_PANEL_MONITOR_DISK_PATH", "/mnt/ultra-media")
MONITOR_DISK_WARNING_PERCENT = float(os.getenv("TORRENT_PANEL_MONITOR_DISK_WARNING_PERCENT", "10"))
MONITOR_DISK_CRITICAL_PERCENT = float(os.getenv("TORRENT_PANEL_MONITOR_DISK_CRITICAL_PERCENT", "5"))
HOMEPAGE_STATUS_URL = os.getenv("TORRENT_PANEL_HOMEPAGE_STATUS_URL", "http://host.docker.internal:3001/")
PROWLARR_PANEL_READY_URL = os.getenv(
    "TORRENT_PANEL_PROWLARR_PANEL_READY_URL",
    f"http://host.docker.internal:3120{PROWLARR_PANEL_PUBLIC_PREFIX or ''}/readyz",
)
PROWLARR_PANEL_OVERVIEW_URL = os.getenv(
    "TORRENT_PANEL_PROWLARR_PANEL_OVERVIEW_URL",
    f"http://host.docker.internal:3120{PROWLARR_PANEL_PUBLIC_PREFIX or ''}/api/overview",
)
PROWLARR_PANEL_HEALTH_URL = os.getenv(
    "TORRENT_PANEL_PROWLARR_PANEL_HEALTH_URL",
    f"http://host.docker.internal:3120{PROWLARR_PANEL_PUBLIC_PREFIX or ''}/api/health",
)
JELLYFIN_STATUS_URL = os.getenv("TORRENT_PANEL_JELLYFIN_STATUS_URL", "http://host.docker.internal:8096/health")
JELLYFIN_PUBLIC_URL = os.getenv("TORRENT_PANEL_JELLYFIN_PUBLIC_URL", "http://127.0.0.1:8096")
RCLONE_RC_URL = os.getenv("TORRENT_PANEL_RCLONE_RC_URL", "http://host.docker.internal:5572/core/stats")
SSH_QBIT_HOST = os.getenv("TORRENT_PANEL_QBIT_TUNNEL_HOST", "host.docker.internal")
SSH_QBIT_PORT = int(os.getenv("TORRENT_PANEL_QBIT_TUNNEL_PORT", "16141"))
SSH_PROWLARR_HOST = os.getenv("TORRENT_PANEL_PROWLARR_TUNNEL_HOST", "host.docker.internal")
SSH_PROWLARR_PORT = int(os.getenv("TORRENT_PANEL_PROWLARR_TUNNEL_PORT", "16124"))
MEDIA_AUTOMATION_ENABLED = os.getenv("TORRENT_PANEL_MEDIA_AUTOMATION_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
MEDIA_AUTOMATION_POLL_SECONDS = float(os.getenv("TORRENT_PANEL_MEDIA_AUTOMATION_POLL_SECONDS", "8"))
MEDIA_AUTOMATION_DEBOUNCE_SECONDS = float(os.getenv("TORRENT_PANEL_MEDIA_AUTOMATION_DEBOUNCE_SECONDS", "45"))
MEDIA_AUTOMATION_JELLYFIN_DELAY_SECONDS = float(os.getenv("TORRENT_PANEL_MEDIA_AUTOMATION_JELLYFIN_DELAY_SECONDS", "5"))
MEDIA_AUTOMATION_MAX_RCLONE_RETRIES = int(os.getenv("TORRENT_PANEL_MEDIA_AUTOMATION_MAX_RCLONE_RETRIES", "3"))
MEDIA_AUTOMATION_MAX_MOUNT_RETRIES = int(os.getenv("TORRENT_PANEL_MEDIA_AUTOMATION_MAX_MOUNT_RETRIES", "5"))
MEDIA_AUTOMATION_MAX_JELLYFIN_RETRIES = int(os.getenv("TORRENT_PANEL_MEDIA_AUTOMATION_MAX_JELLYFIN_RETRIES", "2"))
MEDIA_AUTOMATION_HISTORY_LIMIT = int(os.getenv("TORRENT_PANEL_MEDIA_AUTOMATION_HISTORY_LIMIT", "60"))
MEDIA_AUTOMATION_STATE_PATH = Path(
    os.getenv(
        "TORRENT_PANEL_MEDIA_AUTOMATION_STATE_PATH",
        str(Path(__file__).resolve().parents[1] / "data" / "media-automation-state.json"),
    )
)
MEDIA_MOUNT_PATH = os.getenv("TORRENT_PANEL_MEDIA_MOUNT_PATH", MONITOR_DISK_PATH)
RCLONE_REFRESH_MODE = os.getenv("TORRENT_PANEL_RCLONE_REFRESH_MODE", "auto").strip().lower()
RCLONE_RC_REFRESH_URL = os.getenv("TORRENT_PANEL_RCLONE_RC_REFRESH_URL", "http://host.docker.internal:5572/vfs/refresh")
RCLONE_RC_REFRESH_DIR = os.getenv("TORRENT_PANEL_RCLONE_RC_REFRESH_DIR", "")
RCLONE_SYSTEMD_UNIT = os.getenv("TORRENT_PANEL_RCLONE_SYSTEMD_UNIT", "")
RCLONE_SYSTEMD_RESTART_CMD = os.getenv("TORRENT_PANEL_RCLONE_SYSTEMD_RESTART_CMD", "")
JELLYFIN_API_URL = os.getenv("TORRENT_PANEL_JELLYFIN_API_URL", "http://host.docker.internal:8096")
JELLYFIN_API_KEY = os.getenv("TORRENT_PANEL_JELLYFIN_API_KEY", "")
JELLYFIN_LIBRARY_MAP = os.getenv("TORRENT_PANEL_JELLYFIN_LIBRARY_MAP", "")
JELLYFIN_GLOBAL_FALLBACK = os.getenv("TORRENT_PANEL_JELLYFIN_GLOBAL_FALLBACK", "true").lower() in {"1", "true", "yes", "on"}


class TorrentAction(BaseModel):
    hash: str = Field(..., min_length=40, max_length=64)


class TorrentHashesAction(BaseModel):
    hashes: list[str] = Field(default_factory=list, min_length=1, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def accept_single_hash(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("hash") and not data.get("hashes"):
            return {**data, "hashes": [data["hash"]]}
        return data


class DeleteTorrent(TorrentHashesAction):
    deleteFiles: bool = False


class ForceStartTorrent(TorrentHashesAction):
    enabled: bool = True


class AddMagnet(BaseModel):
    magnet: str | None = Field(default=None, max_length=65535)
    magnets: list[str] = Field(default_factory=list, max_length=50)
    category: str = Field(default="", max_length=80)
    tags: str = Field(default="", max_length=200)
    paused: bool = False
    savePath: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def collect_magnets(self) -> "AddMagnet":
        collected: list[str] = []
        if self.magnet:
            collected.extend(self.magnet.splitlines())
        collected.extend(self.magnets)
        self.magnets = [item.strip() for item in collected if item.strip()]
        return self


class RetryMediaWorkflow(BaseModel):
    scope: str = Field(default="full", pattern="^(full|jellyfin)$")


class ManualMediaActionResult(BaseModel):
    status: str
    message: str


class RateLimiter:
    def __init__(self, max_calls: int, period_seconds: int, max_keys: int) -> None:
        self._max_calls = max_calls
        self._period_seconds = period_seconds
        self._max_keys = max_keys
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._period_seconds:
            hits.popleft()
        if len(hits) >= self._max_calls:
            return False
        hits.append(now)
        self._cleanup(now)
        return True

    def _cleanup(self, now: float) -> None:
        if len(self._hits) <= self._max_keys:
            return
        for key in list(self._hits.keys()):
            hits = self._hits[key]
            while hits and now - hits[0] > self._period_seconds:
                hits.popleft()
            if not hits:
                del self._hits[key]
            if len(self._hits) <= self._max_keys:
                return
        for key in list(self._hits.keys())[: max(0, len(self._hits) - self._max_keys)]:
            del self._hits[key]


def build_client() -> QBittorrentClient:
    return QBittorrentClient(
        QbitConfig(
            url=os.getenv("QBITTORRENT_URL", "http://127.0.0.1:16141"),
            username=os.getenv("QBITTORRENT_USERNAME", ""),
            password=os.getenv("QBITTORRENT_PASSWORD", ""),
            timeout_seconds=float(os.getenv("QBITTORRENT_TIMEOUT_SECONDS", "8")),
        )
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
        rclone_systemd_unit=RCLONE_SYSTEMD_UNIT,
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


app = FastAPI(title="Torrent Panel", docs_url=None, redoc_url=None, openapi_url=None)
api_router = APIRouter()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if PUBLIC_PREFIX:
    app.mount(f"{PUBLIC_PREFIX}/static", StaticFiles(directory=STATIC_DIR), name="prefixed-static")
app.state.qbit = build_client()
app.state.media_automation = MediaAutomationManager(app.state.qbit, build_media_automation_config())
app.state.csrf_tokens = {}
app.state.action_limiter = RateLimiter(
    max_calls=RATE_LIMIT_CALLS,
    period_seconds=RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)
app.state.service_checks = {}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    if INTERNAL_AUTH_SECRET and request.method != "OPTIONS":
        protected_prefixes = ["/api"]
        if PUBLIC_PREFIX:
            protected_prefixes.append(f"{PUBLIC_PREFIX}/api")
        ready_paths = {"/readyz"}
        if PUBLIC_PREFIX:
            ready_paths.add(f"{PUBLIC_PREFIX}/readyz")
        if request.url.path in ready_paths or any(request.url.path.startswith(prefix) for prefix in protected_prefixes):
            received = request.headers.get(INTERNAL_AUTH_HEADER, "")
            if not received or not secrets.compare_digest(received, INTERNAL_AUTH_SECRET):
                return PlainTextResponse("Forbidden", status_code=403)

    response = await call_next(request)
    response.headers["Content-Security-Policy"] = build_csp()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response


@app.on_event("startup")
async def startup() -> None:
    await app.state.media_automation.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state.media_automation.stop()
    await app.state.qbit.close()


def validate_hash(torrent_hash: str) -> str:
    if not HASH_RE.fullmatch(torrent_hash):
        raise HTTPException(
            status_code=422,
            detail=error_detail("hash_invalid", "Hash torrent invalide.", "Réessayer"),
        )
    return torrent_hash.lower()


def validate_hashes(torrent_hashes: list[str]) -> list[str]:
    cleaned = [validate_hash(item) for item in torrent_hashes]
    if not cleaned:
        raise HTTPException(status_code=422, detail=error_detail("hash_invalid", "Aucun torrent sélectionné.", "Réessayer"))
    return cleaned


def validate_magnet(magnet: str) -> tuple[str | None, str | None]:
    candidate = magnet.strip()
    if not candidate.startswith("magnet:?"):
        return None, "Lien magnet invalide."
    if "xt=urn:btih:" not in candidate and "xt=urn:btmh:" not in candidate:
        return None, "Lien magnet sans identifiant torrent."
    return candidate, None


def error_detail(code: str, message: str, recovery: str) -> dict[str, str]:
    return {"code": code, "message": message, "recovery": recovery}


def build_csp() -> str:
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "upgrade-insecure-requests",
        ]
    )


def client_key(request: Request) -> str:
    client_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and client_host in TRUSTED_PROXY_IPS:
        return forwarded.split(",", 1)[0].strip()
    return client_host


def require_action_guard(
    request: Request,
    x_torrent_panel_csrf: str | None = Header(default=None),
) -> None:
    if (
        not x_torrent_panel_csrf
        or not csrf_cookie_matches(request, x_torrent_panel_csrf)
        or not csrf_token_is_valid(request.app, x_torrent_panel_csrf)
    ):
        raise HTTPException(
            status_code=403,
            detail=error_detail("csrf_expired", "Session de protection expirée.", "Actualiser la session"),
        )

    if not request.app.state.action_limiter.allow(client_key(request)):
        raise HTTPException(
            status_code=429,
            detail=error_detail("rate_limited", "Trop d'actions en peu de temps.", "Réessayer"),
        )


def qbit_error_response(exc: QbitError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=error_detail(exc.code, exc.public_message, exc.recovery))


def remember_service_check(service: str, operational: bool) -> tuple[str, str | None]:
    checked_at = now_iso()
    last_successful = app.state.service_checks.get(service)
    if operational:
        last_successful = checked_at
        app.state.service_checks[service] = checked_at
    return checked_at, last_successful


def service_payload(
    name: str,
    status: str,
    message: str,
    *,
    service: str | None = None,
    action: dict[str, str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checked_at, last_successful = remember_service_check(name, status == "operational")
    return {
        "name": name,
        "service": service or name,
        "status": status,
        "message": message,
        "checkedAt": checked_at,
        "lastSuccessfulCheckAt": last_successful,
        "action": action,
        "details": details or {},
    }


async def http_service_status(
    name: str,
    url: str,
    *,
    service: str | None = None,
    ok_statuses: set[int] | None = None,
    action: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        headers = {INTERNAL_AUTH_HEADER: PROWLARR_PANEL_INTERNAL_AUTH_SECRET} if PROWLARR_PANEL_INTERNAL_AUTH_SECRET and "host.docker.internal:3120" in url else None
        async with httpx.AsyncClient(timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS), follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
        allowed = ok_statuses or {200}
        if response.status_code in allowed:
            return service_payload(
                name,
                "operational",
                "Service joignable.",
                service=service,
                action=action or {"kind": "open", "label": "Ouvrir le service", "url": "/"},
                details={"url": url, "httpStatus": response.status_code},
            )
        return service_payload(
            name,
            "degraded",
            f"Réponse HTTP {response.status_code}.",
            service=service,
            action=action or {"kind": "retry", "label": "Réessayer", "url": f"{PUBLIC_PREFIX or ''}/?view=home"},
            details={"url": url, "httpStatus": response.status_code},
        )
    except httpx.HTTPError:
        return service_payload(
            name,
            "unavailable",
            "Service injoignable.",
            service=service,
            action=action or {"kind": "retry", "label": "Réessayer", "url": f"{PUBLIC_PREFIX or ''}/?view=home"},
            details={"url": url},
        )


async def socket_service_status(
    name: str,
    host: str,
    port: int,
    *,
    service: str | None = None,
    action: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=MONITOR_HTTP_TIMEOUT_SECONDS)
        writer.close()
        await writer.wait_closed()
        return service_payload(
            name,
            "operational",
            "Port accessible.",
            service=service,
            action=action or {"kind": "open", "label": "Afficher", "url": f"{PUBLIC_PREFIX or ''}/?view=home"},
            details={"host": host, "port": port},
        )
    except (OSError, TimeoutError):
        return service_payload(
            name,
            "unavailable",
            "Port inaccessible.",
            service=service,
            action=action or {"kind": "retry", "label": "Réessayer", "url": f"{PUBLIC_PREFIX or ''}/?view=home"},
            details={"host": host, "port": port},
        )


def build_alert(
    level: str,
    service: str,
    message: str,
    *,
    action: dict[str, str] | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code or f"{service}:{level}:{abs(hash(message)) % 100000}",
        "severity": level,
        "date": now_iso(),
        "service": service,
        "message": message,
        "action": action,
    }


async def prowlarr_snapshot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        headers = {INTERNAL_AUTH_HEADER: PROWLARR_PANEL_INTERNAL_AUTH_SECRET} if PROWLARR_PANEL_INTERNAL_AUTH_SECRET else None
        async with httpx.AsyncClient(timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS)) as client:
            overview_response, health_response = await asyncio.gather(
                client.get(PROWLARR_PANEL_OVERVIEW_URL, headers=headers),
                client.get(PROWLARR_PANEL_HEALTH_URL, headers=headers),
            )
        overview = overview_response.json() if overview_response.status_code == 200 else {}
        health_payload = health_response.json() if health_response.status_code == 200 else {}
        alerts = health_payload.get("alerts") if isinstance(health_payload, dict) else []
        return overview if isinstance(overview, dict) else {}, alerts if isinstance(alerts, list) else []
    except (httpx.HTTPError, ValueError):
        return {}, []


def normalize_service_status(raw_status: str) -> str:
    if raw_status in {"operational", "degraded", "unavailable"}:
        return raw_status
    return "checking"


async def dashboard_snapshot() -> dict[str, Any]:
    service_results: dict[str, dict[str, Any]] = {
        "Homepage": await http_service_status("Homepage", HOMEPAGE_STATUS_URL, action={"kind": "open", "label": "Ouvrir le service", "url": "/"}),
    }
    home_url = f"{PUBLIC_PREFIX or ''}/?view=home"
    torrents_url = f"{PUBLIC_PREFIX or ''}/?view=torrents"
    prowlarr_search_url = f"{PROWLARR_PANEL_PUBLIC_PREFIX or ''}/?view=search"
    prowlarr_health_url = f"{PROWLARR_PANEL_PUBLIC_PREFIX or ''}/?view=health"
    prowlarr_indexers_url = f"{PROWLARR_PANEL_PUBLIC_PREFIX or ''}/?view=indexers"

    torrent_panel_checked_at, torrent_panel_last_success = remember_service_check("Torrent Panel", True)
    service_results["Torrent Panel"] = {
        "name": "Torrent Panel",
        "service": "Torrent Panel",
        "status": "operational",
        "message": "Interface active.",
        "checkedAt": torrent_panel_checked_at,
        "lastSuccessfulCheckAt": torrent_panel_last_success,
        "action": {"kind": "open", "label": "Afficher", "url": torrents_url},
        "details": {},
    }

    try:
        torrents = await app.state.qbit.torrents()
        qbit_status = service_payload(
            "qBittorrent",
            "operational",
            f"{len(torrents)} torrent(s) récupéré(s).",
            action={"kind": "open", "label": "Ouvrir le service", "url": torrents_url},
            details={"torrentCount": len(torrents)},
        )
    except QbitError as exc:
        torrents = []
        qbit_status = service_payload(
            "qBittorrent",
            "unavailable",
            exc.public_message,
            action={"kind": "retry", "label": "Réessayer", "url": home_url},
            details={"code": exc.code},
        )
    service_results["qBittorrent"] = qbit_status

    service_results["Prowlarr Panel"] = await http_service_status(
        "Prowlarr Panel",
        PROWLARR_PANEL_READY_URL,
        action={"kind": "open", "label": "Ouvrir le service", "url": prowlarr_indexers_url},
    )
    prowlarr_overview, prowlarr_health_alerts = await prowlarr_snapshot()
    prowlarr_status = "operational" if prowlarr_overview.get("connection") == "ready" else "unavailable"
    service_results["Prowlarr"] = service_payload(
        "Prowlarr",
        prowlarr_status,
        "Connexion confirmée." if prowlarr_status == "operational" else "État non confirmé par Prowlarr Panel.",
        action={"kind": "open", "label": "Afficher", "url": prowlarr_health_url},
        details={
            "lastSuccessfulRefresh": prowlarr_overview.get("lastSuccessfulRefresh"),
            "indexersError": prowlarr_overview.get("indexersError", 0),
        },
    )

    service_results["Jellyfin"] = await http_service_status(
        "Jellyfin",
        JELLYFIN_STATUS_URL,
        ok_statuses={200, 204},
        action={"kind": "open", "label": "Ouvrir le service", "url": JELLYFIN_PUBLIC_URL},
    )

    rclone_details: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS)) as client:
            response = await client.post(RCLONE_RC_URL, json={})
        rclone_stats = response.json() if response.status_code == 200 else {}
        rclone_details = rclone_stats if isinstance(rclone_stats, dict) else {}
        error_count = int(rclone_details.get("errors", 0) or 0)
        service_results["rclone"] = service_payload(
            "rclone",
            "degraded" if error_count else "operational",
            f"{error_count} erreur(s) remontée(s)." if error_count else "Statistiques rclone accessibles.",
            action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
            details={"errors": error_count},
        )
    except (httpx.HTTPError, ValueError):
        service_results["rclone"] = service_payload(
            "rclone",
            "unavailable",
            "Endpoint rc inaccessible.",
            action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
        )

    service_results["Tunnel SSH qBittorrent"] = await socket_service_status(
        "Tunnel SSH qBittorrent",
        SSH_QBIT_HOST,
        SSH_QBIT_PORT,
        action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
    )
    service_results["Tunnel SSH Prowlarr"] = await socket_service_status(
        "Tunnel SSH Prowlarr",
        SSH_PROWLARR_HOST,
        SSH_PROWLARR_PORT,
        action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
    )

    try:
        stats = os.statvfs(MONITOR_DISK_PATH)
        total_bytes = stats.f_frsize * stats.f_blocks
        free_bytes = stats.f_frsize * stats.f_bavail
        free_percent = (free_bytes / total_bytes * 100) if total_bytes else 0.0
        if free_percent <= MONITOR_DISK_CRITICAL_PERCENT:
            disk_status = "unavailable"
            disk_message = f"Espace disque critique: {free_percent:.1f}% libre."
        elif free_percent <= MONITOR_DISK_WARNING_PERCENT:
            disk_status = "degraded"
            disk_message = f"Espace disque faible: {free_percent:.1f}% libre."
        else:
            disk_status = "operational"
            disk_message = f"Espace disque suffisant: {free_percent:.1f}% libre."
        service_results["Espace disque"] = service_payload(
            "Espace disque",
            disk_status,
            disk_message,
            service="Stockage",
            action={"kind": "open", "label": "Afficher", "url": "/torrent-panel/?view=home"},
            details={"path": MONITOR_DISK_PATH, "freePercent": round(free_percent, 1)},
        )
    except OSError:
        service_results["Espace disque"] = service_payload(
            "Espace disque",
            "checking",
            "Chemin de surveillance indisponible.",
            service="Stockage",
            action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
            details={"path": MONITOR_DISK_PATH},
        )

    alerts: list[dict[str, Any]] = []
    blocked_torrents = [torrent for torrent in torrents if state_meta_from_qbit(torrent) == "error"]
    if blocked_torrents:
        alerts.append(
            build_alert(
                "critical",
                "qBittorrent",
                f"{len(blocked_torrents)} torrent(s) bloqué(s) ou en erreur.",
                action={"kind": "open", "label": "Afficher", "url": f"{torrents_url}&status=error"},
                code="qbit_blocked",
            )
        )
    indexer_errors = int(prowlarr_overview.get("indexersError", 0) or 0)
    if indexer_errors:
        alerts.append(
            build_alert(
                "critical",
                "Prowlarr",
                f"{indexer_errors} indexeur(s) indisponible(s).",
                action={"kind": "open", "label": "Afficher", "url": prowlarr_health_url},
                code="prowlarr_indexers_error",
            )
        )
    for item in prowlarr_health_alerts[:6]:
        alerts.append(
            build_alert(
                "warning",
                "Prowlarr",
                str(item.get("message") or item.get("type") or "Alerte Prowlarr."),
                action={"kind": "open", "label": "Afficher", "url": prowlarr_health_url},
                code=f"prowlarr_health_{item.get('type', 'warning')}",
            )
        )
    rclone_errors = int(rclone_details.get("errors", 0) or 0)
    if rclone_errors:
        alerts.append(
            build_alert(
                "warning",
                "rclone",
                f"{rclone_errors} erreur(s) rclone détectée(s).",
                action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
                code="rclone_errors",
            )
        )
    disk_service = service_results["Espace disque"]
    if normalize_service_status(disk_service["status"]) != "operational":
        alerts.append(
            build_alert(
                "critical" if disk_service["status"] == "unavailable" else "warning",
                "Stockage",
                disk_service["message"],
                action=disk_service["action"],
                code="disk_state",
            )
        )
    for tunnel_name in ("Tunnel SSH qBittorrent", "Tunnel SSH Prowlarr"):
        tunnel_status = service_results[tunnel_name]
        if tunnel_status["status"] != "operational":
            alerts.append(
                build_alert(
                    "critical",
                    tunnel_name,
                    f"{tunnel_name} inaccessible.",
                    action=tunnel_status["action"],
                    code=tunnel_name.lower().replace(" ", "_"),
                )
            )
    recent_failure_services = [item for item in service_results.values() if item["status"] == "unavailable"]
    if recent_failure_services:
        first_failure = recent_failure_services[0]
        alerts.append(
            build_alert(
                "warning",
                first_failure["name"],
                f"Échec récent de vérification sur {first_failure['name'].lower()}.",
                action=first_failure["action"],
                code=f"recent_failure_{first_failure['name'].lower()}",
            )
        )
    alerts.extend(app.state.media_automation.dashboard_alerts())

    critical_alerts = [item for item in alerts if item["severity"] == "critical"]
    return {
        "generatedAt": now_iso(),
        "alerts": alerts,
        "criticalCount": len(critical_alerts),
        "services": list(service_results.values()),
        "mediaAutomation": app.state.media_automation.snapshot(),
        "quickActions": [
            {"id": "add-torrent", "label": "Ajouter un torrent", "url": f"{torrents_url}&add=1"},
            {"id": "search-release", "label": "Rechercher une release", "url": prowlarr_search_url},
            {"id": "blocked-torrents", "label": "Voir les torrents bloqués", "url": f"{torrents_url}&status=error"},
            {"id": "test-indexers", "label": "Tester les indexeurs", "url": f"{prowlarr_indexers_url}&confirm=test-all"},
            {"id": "open-jellyfin", "label": "Ouvrir Jellyfin", "url": JELLYFIN_PUBLIC_URL},
            {
                "id": "refresh-rclone-manual",
                "label": "Actualiser rclone",
                "kind": "api",
                "actionId": "rclone-refresh",
                "description": "Déclenche un refresh manuel du montage rclone.",
            },
            {
                "id": "refresh-jellyfin-manual",
                "label": "Scanner Jellyfin",
                "kind": "api",
                "actionId": "jellyfin-refresh",
                "description": "Déclenche un scan manuel des bibliothèques Jellyfin.",
            },
            {"id": "media-history", "label": "Historique médias", "url": f"{home_url}#mediaAutomation"},
            {"id": "refresh-all", "label": "Actualiser tous les services", "url": f"{home_url}&refresh=1"},
        ],
    }


def state_meta_from_qbit(torrent: dict[str, Any]) -> str:
    raw = str(torrent.get("state") or "unknown")
    if raw in {"stalledDL", "stalledUP", "error", "missingFiles"}:
        return "error"
    if raw in {"downloading", "forcedDL", "metaDL"}:
        return "downloading"
    if raw in {"uploading", "forcedUP"}:
        return "sharing"
    if raw in {"checkingDL", "checkingUP", "checkingResumeData"}:
        return "checking"
    if raw in {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}:
        return "paused"
    return "waiting"


def cleanup_csrf_tokens(app_instance: FastAPI, now: float | None = None) -> None:
    current = now if now is not None else time.monotonic()
    tokens: dict[str, float] = app_instance.state.csrf_tokens
    for token, created_at in list(tokens.items()):
        if current - created_at > CSRF_TOKEN_TTL_SECONDS:
            del tokens[token]
    if len(tokens) <= MAX_CSRF_TOKENS:
        return
    for token, _created_at in sorted(tokens.items(), key=lambda item: item[1])[: len(tokens) - MAX_CSRF_TOKENS]:
        del tokens[token]


def csrf_token_is_valid(app_instance: FastAPI, token: str) -> bool:
    cleanup_csrf_tokens(app_instance)
    return token in app_instance.state.csrf_tokens


def csrf_cookie_matches(request: Request, token: str) -> bool:
    """Accept the token when any same-name cookie matches it.

    Browsers may retain cookies with the same name but different paths after a
    reverse-proxy or public-prefix change. ``request.cookies`` collapses those
    duplicates and can select the stale value, making session renewal fail
    forever even though the browser also sent the fresh cookie.
    """
    for item in request.headers.get("cookie", "").split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name == CSRF_COOKIE and secrets.compare_digest(value, token):
            return True
    return False


def set_csrf_cookie(request: Request, response: Response) -> str:
    is_https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    cleanup_csrf_tokens(app)
    token = secrets.token_urlsafe(32)
    app.state.csrf_tokens[token] = time.monotonic()
    response.set_cookie(
        CSRF_COOKIE,
        token,
        secure=is_https,
        httponly=False,
        samesite="strict",
        path=f"{PUBLIC_PREFIX}/" if PUBLIC_PREFIX else "/",
    )
    return token


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config.js")
async def config_js() -> PlainTextResponse:
    return PlainTextResponse(
        "\n".join(
            [
                "window.__TORRENT_PANEL_CONFIG__ = {",
                f'  publicPrefix: "{PUBLIC_PREFIX or ""}",',
                f'  prowlarrPanelPrefix: "{PROWLARR_PANEL_PUBLIC_PREFIX or ""}",',
                "};",
            ]
        ),
        media_type="application/javascript",
    )


if PUBLIC_PREFIX:

    @app.get(PUBLIC_PREFIX)
    async def prefixed_index_redirect() -> RedirectResponse:
        return RedirectResponse(url=f"{PUBLIC_PREFIX}/", status_code=308)


    @app.get(f"{PUBLIC_PREFIX}/")
    async def prefixed_index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")


    @app.get(f"{PUBLIC_PREFIX}/config.js")
    async def prefixed_config_js() -> PlainTextResponse:
        return await config_js()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


if PUBLIC_PREFIX:

    @app.get(f"{PUBLIC_PREFIX}/healthz")
    async def prefixed_healthz() -> dict[str, str]:
        return await healthz()


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    try:
        await app.state.qbit.ready()
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "ready"}


if PUBLIC_PREFIX:

    @app.get(f"{PUBLIC_PREFIX}/readyz")
    async def prefixed_readyz() -> dict[str, str]:
        return await readyz()


@api_router.get("/session")
async def session(request: Request, response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"csrfToken": set_csrf_cookie(request, response)}


@api_router.get("/torrents")
async def torrents() -> dict[str, object]:
    try:
        return {"torrents": await app.state.qbit.torrents()}
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@api_router.get("/dashboard")
async def dashboard() -> dict[str, Any]:
    return await dashboard_snapshot()


@api_router.get("/media-workflows")
async def media_workflows() -> dict[str, Any]:
    return app.state.media_automation.snapshot()


@api_router.post("/torrents/pause", dependencies=[Depends(require_action_guard)])
async def pause_torrent(payload: TorrentHashesAction) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await app.state.qbit.pause_many(hashes)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "paused", "count": len(hashes)}


@api_router.post("/torrents/resume", dependencies=[Depends(require_action_guard)])
async def resume_torrent(payload: TorrentHashesAction) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await app.state.qbit.resume_many(hashes)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "resumed", "count": len(hashes)}


@api_router.post("/torrents/delete", dependencies=[Depends(require_action_guard)])
async def delete_torrent(payload: DeleteTorrent) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await app.state.qbit.delete_many(hashes, payload.deleteFiles)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "deleted", "count": len(hashes)}


@api_router.post("/torrents/force-start", dependencies=[Depends(require_action_guard)])
async def force_start_torrent(payload: ForceStartTorrent) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await app.state.qbit.set_force_start_many(hashes, payload.enabled)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "force_start_updated", "enabled": payload.enabled, "count": len(hashes)}


@api_router.post("/torrents/add", dependencies=[Depends(require_action_guard)])
async def add_torrent(payload: AddMagnet) -> dict[str, object]:
    if payload.savePath and payload.savePath not in ALLOWED_SAVE_PATHS:
        raise HTTPException(
            status_code=422,
            detail=error_detail("save_path_refused", "Chemin de sauvegarde non autorisé.", "Réessayer"),
        )

    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    for index, magnet in enumerate(payload.magnets, start=1):
        valid_magnet, reason = validate_magnet(magnet)
        if valid_magnet:
            accepted.append(valid_magnet)
        else:
            rejected.append({"line": str(index), "reason": reason or "Lien magnet invalide."})

    if not accepted:
        return {"status": "rejected", "accepted": 0, "rejected": rejected}

    try:
        await app.state.qbit.add_magnet(
            "\n".join(accepted),
            category=payload.category.strip(),
            tags=payload.tags.strip(),
            paused=payload.paused,
            save_path=payload.savePath.strip(),
        )
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "added", "accepted": len(accepted), "rejected": rejected}


@api_router.post("/media-workflows/{entry_id}/retry", dependencies=[Depends(require_action_guard)])
async def retry_media_workflow(entry_id: str, payload: RetryMediaWorkflow) -> dict[str, Any]:
    return {"entry": await app.state.media_automation.retry(entry_id, payload.scope)}


@api_router.post("/media-actions/{action}", dependencies=[Depends(require_action_guard)])
async def trigger_manual_media_action(action: str) -> dict[str, str]:
    try:
        return await app.state.media_automation.manual_action(action)
    except MediaAutomationError as exc:
        raise HTTPException(
            status_code=502,
            detail=error_detail("media_action_failed", exc.public_message, "Réessayer"),
        ) from exc


app.include_router(api_router, prefix="/api")
if PUBLIC_PREFIX:
    app.include_router(api_router, prefix=f"{PUBLIC_PREFIX}/api")
