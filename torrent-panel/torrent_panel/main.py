import asyncio
import logging
import os
import re
import secrets
import time
from datetime import UTC, datetime
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .qbittorrent import QBittorrentClient, QbitConfig, QbitError

logging.basicConfig(level=os.getenv("TORRENT_PANEL_LOG_LEVEL", "INFO"))
logger = logging.getLogger("torrent_panel")

STATIC_DIR = Path(__file__).parent / "static"
HASH_RE = re.compile(r"^[A-Fa-f0-9]{40}([A-Fa-f0-9]{24})?$")
PUBLIC_PREFIX = os.getenv("TORRENT_PANEL_PUBLIC_PREFIX", "/torrent-panel").rstrip("/")
CSRF_COOKIE = "torrent_panel_csrf"
CSRF_HEADER = "X-Torrent-Panel-CSRF"
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
    "http://host.docker.internal:3120/prowlarr-panel/readyz",
)
PROWLARR_PANEL_OVERVIEW_URL = os.getenv(
    "TORRENT_PANEL_PROWLARR_PANEL_OVERVIEW_URL",
    "http://host.docker.internal:3120/prowlarr-panel/api/overview",
)
JELLYFIN_STATUS_URL = os.getenv("TORRENT_PANEL_JELLYFIN_STATUS_URL", "http://host.docker.internal:8096/health")
JELLYFIN_PUBLIC_URL = os.getenv("TORRENT_PANEL_JELLYFIN_PUBLIC_URL", "http://127.0.0.1:8096")
RCLONE_RC_URL = os.getenv("TORRENT_PANEL_RCLONE_RC_URL", "http://host.docker.internal:5572/core/stats")
SSH_QBIT_HOST = os.getenv("TORRENT_PANEL_QBIT_TUNNEL_HOST", "host.docker.internal")
SSH_QBIT_PORT = int(os.getenv("TORRENT_PANEL_QBIT_TUNNEL_PORT", "16141"))
SSH_PROWLARR_HOST = os.getenv("TORRENT_PANEL_PROWLARR_TUNNEL_HOST", "host.docker.internal")
SSH_PROWLARR_PORT = int(os.getenv("TORRENT_PANEL_PROWLARR_TUNNEL_PORT", "16124"))


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


app = FastAPI(title="Torrent Panel", docs_url=None, redoc_url=None, openapi_url=None)
api_router = APIRouter()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/torrent-panel/static", StaticFiles(directory=STATIC_DIR), name="prefixed-static")
app.state.qbit = build_client()
app.state.csrf_tokens = {}
app.state.action_limiter = RateLimiter(
    max_calls=RATE_LIMIT_CALLS,
    period_seconds=RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)
app.state.service_checks = {}


@app.on_event("shutdown")
async def shutdown() -> None:
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS), follow_redirects=True) as client:
            response = await client.get(url)
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
            action=action or {"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
            details={"url": url, "httpStatus": response.status_code},
        )
    except httpx.HTTPError:
        return service_payload(
            name,
            "unavailable",
            "Service injoignable.",
            service=service,
            action=action or {"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
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
            action=action or {"kind": "open", "label": "Afficher", "url": "/torrent-panel/?view=home"},
            details={"host": host, "port": port},
        )
    except (OSError, TimeoutError):
        return service_payload(
            name,
            "unavailable",
            "Port inaccessible.",
            service=service,
            action=action or {"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS)) as client:
            overview_response, health_response = await asyncio.gather(
                client.get(PROWLARR_PANEL_OVERVIEW_URL),
                client.get("http://host.docker.internal:3120/prowlarr-panel/api/health"),
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

    torrent_panel_checked_at, torrent_panel_last_success = remember_service_check("Torrent Panel", True)
    service_results["Torrent Panel"] = {
        "name": "Torrent Panel",
        "service": "Torrent Panel",
        "status": "operational",
        "message": "Interface active.",
        "checkedAt": torrent_panel_checked_at,
        "lastSuccessfulCheckAt": torrent_panel_last_success,
        "action": {"kind": "open", "label": "Afficher", "url": "/torrent-panel/?view=torrents"},
        "details": {},
    }

    try:
        torrents = await app.state.qbit.torrents()
        qbit_status = service_payload(
            "qBittorrent",
            "operational",
            f"{len(torrents)} torrent(s) récupéré(s).",
            action={"kind": "open", "label": "Ouvrir le service", "url": "/torrent-panel/?view=torrents"},
            details={"torrentCount": len(torrents)},
        )
    except QbitError as exc:
        torrents = []
        qbit_status = service_payload(
            "qBittorrent",
            "unavailable",
            exc.public_message,
            action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
            details={"code": exc.code},
        )
    service_results["qBittorrent"] = qbit_status

    service_results["Prowlarr Panel"] = await http_service_status(
        "Prowlarr Panel",
        PROWLARR_PANEL_READY_URL,
        action={"kind": "open", "label": "Ouvrir le service", "url": "/prowlarr-panel/?view=indexers"},
    )
    prowlarr_overview, prowlarr_health_alerts = await prowlarr_snapshot()
    prowlarr_status = "operational" if prowlarr_overview.get("connection") == "ready" else "unavailable"
    service_results["Prowlarr"] = service_payload(
        "Prowlarr",
        prowlarr_status,
        "Connexion confirmée." if prowlarr_status == "operational" else "État non confirmé par Prowlarr Panel.",
        action={"kind": "open", "label": "Afficher", "url": "/prowlarr-panel/?view=health"},
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
                action={"kind": "open", "label": "Afficher", "url": "/torrent-panel/?view=torrents&status=error"},
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
                action={"kind": "open", "label": "Afficher", "url": "/prowlarr-panel/?view=health"},
                code="prowlarr_indexers_error",
            )
        )
    for item in prowlarr_health_alerts[:6]:
        alerts.append(
            build_alert(
                "warning",
                "Prowlarr",
                str(item.get("message") or item.get("type") or "Alerte Prowlarr."),
                action={"kind": "open", "label": "Afficher", "url": "/prowlarr-panel/?view=health"},
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

    critical_alerts = [item for item in alerts if item["severity"] == "critical"]
    return {
        "generatedAt": now_iso(),
        "alerts": alerts,
        "criticalCount": len(critical_alerts),
        "services": list(service_results.values()),
        "quickActions": [
            {"id": "add-torrent", "label": "Ajouter un torrent", "url": "/torrent-panel/?view=torrents&add=1"},
            {"id": "search-release", "label": "Rechercher une release", "url": "/prowlarr-panel/?view=search"},
            {"id": "blocked-torrents", "label": "Voir les torrents bloqués", "url": "/torrent-panel/?view=torrents&status=error"},
            {"id": "test-indexers", "label": "Tester les indexeurs", "url": "/prowlarr-panel/?view=indexers&test=all"},
            {"id": "open-jellyfin", "label": "Ouvrir Jellyfin", "url": JELLYFIN_PUBLIC_URL},
            {"id": "refresh-all", "label": "Actualiser tous les services", "url": "/torrent-panel/?view=home&refresh=1"},
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
@app.get("/torrent-panel")
@app.get("/torrent-panel/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
@app.get("/torrent-panel/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
@app.get("/torrent-panel/readyz")
async def readyz() -> dict[str, str]:
    try:
        await app.state.qbit.ready()
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "ready"}


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


app.include_router(api_router, prefix="/api")
if PUBLIC_PREFIX:
    app.include_router(api_router, prefix=f"{PUBLIC_PREFIX}/api")
