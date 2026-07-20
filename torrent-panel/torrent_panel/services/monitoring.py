"""Monitoring / snapshot functions: dashboard, storage, Jellyfin, health, activity."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from fastapi import FastAPI

from ..config import (
    ACTIVITY_PUBLIC_PREFIX,
    HEALTH_PUBLIC_PREFIX,
    HOMEPAGE_STATUS_URL,
    JELLYFIN_API_KEY,
    JELLYFIN_API_URL,
    JELLYFIN_PUBLIC_URL,
    MEDIA_PUBLIC_PREFIX,
    MONITOR_DISK_CRITICAL_PERCENT,
    MONITOR_DISK_PATH,
    MONITOR_DISK_WARNING_PERCENT,
    MONITOR_HTTP_TIMEOUT_SECONDS,
    PROWLARR_PANEL_HEALTH_URL,
    PROWLARR_PANEL_OVERVIEW_URL,
    PROWLARR_PANEL_PUBLIC_PREFIX,
    PROWLARR_PANEL_READY_URL,
    PUBLIC_PREFIX,
    RCLONE_RC_URL,
    SSH_QBIT_HOST,
    SSH_QBIT_PORT,
    SSH_PROWLARR_HOST,
    SSH_PROWLARR_PORT,
    STORAGE_PUBLIC_PREFIX,
)
from ..qbittorrent import QbitError
from .media_automation import now_iso


def format_bytes(value: int | float) -> str:
    units = ["o", "Ko", "Mo", "Go", "To"]
    amount = float(value or 0)
    if amount <= 0:
        return "0 o"
    unit_index = 0
    while amount >= 1024 and unit_index < len(units) - 1:
        amount /= 1024
        unit_index += 1
    precision = 0 if unit_index == 0 else 1
    return f"{amount:.{precision}f} {units[unit_index]}"


def route_url(prefix: str, path: str = "/") -> str:
    base = prefix or ""
    if path == "/":
        return f"{base}/"
    return f"{base}{path}"


def normalize_service_status(raw_status: str) -> str:
    if raw_status in {"operational", "degraded", "unavailable"}:
        return raw_status
    return "checking"


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


def remember_service_check(app: FastAPI, service: str, operational: bool) -> tuple[str, str | None]:
    checked_at = now_iso()
    last_successful = app.state.service_checks.get(service)
    if operational:
        last_successful = checked_at
        app.state.service_checks[service] = checked_at
    return checked_at, last_successful


def service_payload(
    app: FastAPI,
    name: str,
    status: str,
    message: str,
    *,
    service: str | None = None,
    action: dict[str, str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checked_at, last_successful = remember_service_check(app, name, status == "operational")
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
    app: FastAPI,
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
                app,
                name,
                "operational",
                "Service joignable.",
                service=service,
                action=action or {"kind": "open", "label": "Ouvrir le service", "url": "/"},
                details={"url": url, "httpStatus": response.status_code},
            )
        return service_payload(
            app,
            name,
            "degraded",
            f"Réponse HTTP {response.status_code}.",
            service=service,
            action=action or {"kind": "retry", "label": "Réessayer", "url": f"{PUBLIC_PREFIX or ''}/?view=home"},
            details={"url": url, "httpStatus": response.status_code},
        )
    except httpx.HTTPError:
        return service_payload(
            app,
            name,
            "unavailable",
            "Service injoignable.",
            service=service,
            action=action or {"kind": "retry", "label": "Réessayer", "url": f"{PUBLIC_PREFIX or ''}/?view=home"},
            details={"url": url},
        )


async def socket_service_status(
    app: FastAPI,
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
            app,
            name,
            "operational",
            "Port accessible.",
            service=service,
            action=action or {"kind": "open", "label": "Afficher", "url": f"{PUBLIC_PREFIX or ''}/?view=home"},
            details={"host": host, "port": port},
        )
    except (OSError, TimeoutError):
        return service_payload(
            app,
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS)) as client:
            overview_response, health_response = await asyncio.gather(
                client.get(PROWLARR_PANEL_OVERVIEW_URL),
                client.get(PROWLARR_PANEL_HEALTH_URL),
            )
        overview = overview_response.json() if overview_response.status_code == 200 else {}
        health_payload = health_response.json() if health_response.status_code == 200 else {}
        alerts = health_payload.get("alerts") if isinstance(health_payload, dict) else []
        return overview if isinstance(overview, dict) else {}, alerts if isinstance(alerts, list) else []
    except (httpx.HTTPError, ValueError):
        return {}, []


async def dashboard_snapshot(app: FastAPI) -> dict[str, Any]:
    qbit = app.state.qbit
    media_automation = app.state.media_automation
    service_results: dict[str, dict[str, Any]] = {
        "Homepage": await http_service_status(app, "Homepage", HOMEPAGE_STATUS_URL, action={"kind": "open", "label": "Ouvrir le service", "url": "/"}),
    }
    home_url = f"{PUBLIC_PREFIX or ''}/?view=home"
    torrents_url = f"{PUBLIC_PREFIX or ''}/?view=torrents"
    prowlarr_search_url = f"{PROWLARR_PANEL_PUBLIC_PREFIX or ''}/?view=search"
    prowlarr_health_url = f"{PROWLARR_PANEL_PUBLIC_PREFIX or ''}/?view=health"
    prowlarr_indexers_url = f"{PROWLARR_PANEL_PUBLIC_PREFIX or ''}/?view=indexers"

    torrent_panel_checked_at, torrent_panel_last_success = remember_service_check(app, "Torrent Panel", True)
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
        torrents = await qbit.torrents()
        qbit_status = service_payload(
            app,
            "qBittorrent",
            "operational",
            f"{len(torrents)} torrent(s) récupéré(s).",
            action={"kind": "open", "label": "Ouvrir le service", "url": torrents_url},
            details={"torrentCount": len(torrents)},
        )
    except QbitError as exc:
        torrents = []
        qbit_status = service_payload(
            app,
            "qBittorrent",
            "unavailable",
            exc.public_message,
            action={"kind": "retry", "label": "Réessayer", "url": home_url},
            details={"code": exc.code},
        )
    service_results["qBittorrent"] = qbit_status

    service_results["Prowlarr Panel"] = await http_service_status(
        app,
        "Prowlarr Panel",
        PROWLARR_PANEL_READY_URL,
        action={"kind": "open", "label": "Ouvrir le service", "url": prowlarr_indexers_url},
    )
    prowlarr_overview, prowlarr_health_alerts = await prowlarr_snapshot()
    prowlarr_status = "operational" if prowlarr_overview.get("connection") == "ready" else "unavailable"
    service_results["Prowlarr"] = service_payload(
        app,
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
        app,
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
            app,
            "rclone",
            "degraded" if error_count else "operational",
            f"{error_count} erreur(s) remontée(s)." if error_count else "Statistiques rclone accessibles.",
            action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
            details={"errors": error_count},
        )
    except (httpx.HTTPError, ValueError):
        service_results["rclone"] = service_payload(
            app,
            "rclone",
            "unavailable",
            "Endpoint rc inaccessible.",
            action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
        )

    service_results["Tunnel SSH qBittorrent"] = await socket_service_status(
        app,
        "Tunnel SSH qBittorrent",
        SSH_QBIT_HOST,
        SSH_QBIT_PORT,
        action={"kind": "retry", "label": "Réessayer", "url": "/torrent-panel/?view=home"},
    )
    service_results["Tunnel SSH Prowlarr"] = await socket_service_status(
        app,
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
            app,
            "Espace disque",
            disk_status,
            disk_message,
            service="Stockage",
            action={"kind": "open", "label": "Afficher", "url": "/torrent-panel/?view=home"},
            details={"path": MONITOR_DISK_PATH, "freePercent": round(free_percent, 1)},
        )
    except OSError:
        service_results["Espace disque"] = service_payload(
            app,
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
    alerts.extend(media_automation.dashboard_alerts())

    critical_alerts = [item for item in alerts if item["severity"] == "critical"]
    return {
        "generatedAt": now_iso(),
        "alerts": alerts,
        "criticalCount": len(critical_alerts),
        "services": list(service_results.values()),
        "mediaAutomation": media_automation.snapshot(),
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


async def storage_snapshot(app: FastAPI) -> dict[str, Any]:
    generated_at = now_iso()
    disk: dict[str, Any]
    try:
        stats = os.statvfs(MONITOR_DISK_PATH)
        total_bytes = stats.f_frsize * stats.f_blocks
        free_bytes = stats.f_frsize * stats.f_bavail
        used_bytes = max(0, total_bytes - free_bytes)
        used_percent = (used_bytes / total_bytes * 100) if total_bytes else 0.0
        status = "critical" if (100 - used_percent) <= MONITOR_DISK_CRITICAL_PERCENT else "warning" if (100 - used_percent) <= MONITOR_DISK_WARNING_PERCENT else "normal"
        disk = {
            "path": MONITOR_DISK_PATH,
            "mounted": True,
            "status": status,
            "totalBytes": total_bytes,
            "usedBytes": used_bytes,
            "freeBytes": free_bytes,
            "usedPercent": round(used_percent, 1),
            "freePercent": round(100 - used_percent, 1),
            "estimateToFull": None,
        }
    except OSError:
        disk = {
            "path": MONITOR_DISK_PATH,
            "mounted": False,
            "status": "critical",
            "totalBytes": 0,
            "usedBytes": 0,
            "freeBytes": 0,
            "usedPercent": 0.0,
            "freePercent": 0.0,
            "estimateToFull": None,
        }

    rclone_stats: dict[str, Any] = {}
    rclone_error = ""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS)) as client:
            response = await client.post(RCLONE_RC_URL, json={})
        parsed = response.json() if response.status_code == 200 else {}
        rclone_stats = parsed if isinstance(parsed, dict) else {}
    except (httpx.HTTPError, ValueError):
        rclone_error = "Endpoint RC rclone inaccessible."

    transfers = rclone_stats.get("transferring")
    queue = rclone_stats.get("checking") or rclone_stats.get("transfers") or 0
    active_transfers = transfers if isinstance(transfers, list) else []
    speed = int(rclone_stats.get("speed", 0) or 0)
    if speed > 0 and disk.get("freeBytes"):
        disk["estimateToFull"] = round(disk["freeBytes"] / speed)

    return {
        "generatedAt": generated_at,
        "disk": disk,
        "rclone": {
            "status": "error" if rclone_error else ("warning" if int(rclone_stats.get("errors", 0) or 0) else "ok"),
            "lastSuccessfulResponseAt": generated_at if not rclone_error else None,
            "speedBytes": speed,
            "speedLabel": format_bytes(speed) + "/s",
            "bytesTransferred": int(rclone_stats.get("bytes", 0) or 0),
            "errors": int(rclone_stats.get("errors", 0) or 0),
            "queue": queue,
            "transfersActive": len(active_transfers),
            "transfers": active_transfers,
            "errorMessage": rclone_error,
        },
    }


async def jellyfin_snapshot() -> dict[str, Any]:
    generated_at = now_iso()
    summary = {
        "generatedAt": generated_at,
        "status": "unavailable",
        "version": "inconnue",
        "serverName": "Jellyfin",
        "sessions": [],
        "activeUsers": [],
        "libraries": [],
        "recentItems": [],
        "tasks": [],
        "errors": [],
        "actions": {
            "openUrl": JELLYFIN_PUBLIC_URL,
            "scanEndpointAvailable": bool(JELLYFIN_API_KEY),
        },
    }
    if not JELLYFIN_API_KEY:
        summary["errors"] = ["Clé API Jellyfin absente côté backend."]
        return summary

    headers = {"X-Emby-Token": JELLYFIN_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(MONITOR_HTTP_TIMEOUT_SECONDS), trust_env=False) as client:
            info_response, session_response, views_response, latest_response, tasks_response = await asyncio.gather(
                client.get(f"{JELLYFIN_API_URL}/System/Info/Public", headers=headers),
                client.get(f"{JELLYFIN_API_URL}/Sessions", headers=headers),
                client.get(f"{JELLYFIN_API_URL}/Users", headers=headers),
                client.get(f"{JELLYFIN_API_URL}/Items/Latest", headers=headers, params={"Limit": 8}),
                client.get(f"{JELLYFIN_API_URL}/ScheduledTasks", headers=headers),
            )
        info = info_response.json() if info_response.status_code == 200 else {}
        sessions = session_response.json() if session_response.status_code == 200 else []
        users = views_response.json() if views_response.status_code == 200 else []
        latest = latest_response.json() if latest_response.status_code == 200 else []
        tasks = tasks_response.json() if tasks_response.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        summary["errors"] = ["API Jellyfin indisponible."]
        return summary

    summary["status"] = "operational"
    summary["version"] = str(info.get("Version") or info.get("version") or "inconnue")
    summary["serverName"] = str(info.get("ServerName") or info.get("serverName") or "Jellyfin")
    summary["sessions"] = [item for item in sessions if isinstance(item, dict)]
    summary["activeUsers"] = [
        {"name": str(item.get("Name") or item.get("name") or ""), "id": str(item.get("Id") or item.get("id") or "")}
        for item in users if isinstance(item, dict)
    ]
    summary["recentItems"] = [
        {"name": str(item.get("Name") or item.get("name") or "Media"), "type": str(item.get("Type") or item.get("type") or ""), "id": str(item.get("Id") or item.get("id") or "")}
        for item in latest if isinstance(item, dict)
    ]
    summary["tasks"] = [
        {
            "name": str(item.get("Name") or item.get("name") or "Tâche"),
            "state": str(item.get("State") or item.get("state") or ""),
            "lastExecutionResult": str(item.get("LastExecutionResult") or ""),
            "isRunning": bool(item.get("IsRunning") or item.get("isRunning")),
        }
        for item in tasks if isinstance(item, dict)
    ]
    return summary


async def health_snapshot(app: FastAPI) -> dict[str, Any]:
    dashboard = await dashboard_snapshot(app)
    services = list(dashboard.get("services", []))
    operational = [item for item in services if item.get("status") == "operational"]
    unavailable = [item for item in services if item.get("status") == "unavailable"]
    degraded = [item for item in services if item.get("status") == "degraded"]
    global_status = "indisponible" if unavailable else "dégradé" if degraded else "opérationnel"
    checks = []
    for item in services:
        checks.append(
            {
                "name": item.get("name"),
                "service": item.get("service"),
                "liveness": "ok" if item.get("name") in {"Torrent Panel", "Homepage", "Prowlarr Panel"} else ("ok" if item.get("status") != "unavailable" else "error"),
                "readiness": normalize_service_status(str(item.get("status") or "")),
                "message": item.get("message"),
                "latency": item.get("details", {}).get("httpStatus"),
                "lastSuccessfulCheckAt": item.get("lastSuccessfulCheckAt"),
                "lastError": item.get("message") if item.get("status") != "operational" else "",
            }
        )
    return {
        "generatedAt": dashboard.get("generatedAt"),
        "globalStatus": global_status,
        "summary": {"operational": len(operational), "degraded": len(degraded), "unavailable": len(unavailable)},
        "checks": checks,
        "alerts": dashboard.get("alerts", []),
    }


async def activity_snapshot(app: FastAPI) -> dict[str, Any]:
    qbit = app.state.qbit
    media_automation = app.state.media_automation
    notifications = app.state.notifications
    automation_rules = app.state.automation_rules
    dashboard = await dashboard_snapshot(app)
    storage = await storage_snapshot(app)
    media = await jellyfin_snapshot()
    torrents = []
    try:
        torrents = await qbit.torrents()
    except QbitError:
        torrents = []
    prowlarr_overview, _prowlarr_health = await prowlarr_snapshot()
    media_history = media_automation.snapshot().get("entries", [])
    summary = {
        "downloadsActive": len([item for item in torrents if state_meta_from_qbit(item) == "downloading"]),
        "downloadSpeedBytes": sum(int(item.get("downloadSpeed", 0) or 0) for item in torrents),
        "uploadSpeedBytes": sum(int(item.get("uploadSpeed", 0) or 0) for item in torrents),
        "completedRecently": len([item for item in torrents if int(item.get("completionOn", 0) or 0) > 0]),
        "blockedTorrents": len([item for item in torrents if state_meta_from_qbit(item) == "error"]),
        "indexersError": int(prowlarr_overview.get("indexersError", 0) or 0),
        "transfersRcloneActive": int(storage.get("rclone", {}).get("transfersActive", 0) or 0),
        "rcloneErrors": int(storage.get("rclone", {}).get("errors", 0) or 0),
        "diskFreeBytes": int(storage.get("disk", {}).get("freeBytes", 0) or 0),
        "jellyfinRecentItems": len(media.get("recentItems", [])),
        "backendUnavailable": len([item for item in dashboard.get("services", []) if item.get("status") == "unavailable"]),
    }
    timeline: list[dict[str, Any]] = []
    for alert in dashboard.get("alerts", [])[:10]:
        timeline.append(
            {
                "date": alert.get("date"),
                "service": alert.get("service"),
                "type": "service_alert",
                "message": alert.get("message"),
                "result": alert.get("severity"),
                "origin": "automatique",
            }
        )
    for entry in media_history[:10]:
        timeline.append(
            {
                "date": entry.get("updatedAt") or entry.get("completedAt"),
                "service": "Automatisation médias",
                "type": "download_completed",
                "message": entry.get("torrentName"),
                "result": entry.get("stateLabel"),
                "origin": "automatique",
            }
        )
    timeline.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    notifications_snapshot = notifications.reconcile(list(dashboard.get("alerts", [])))
    simulations = automation_rules.simulate(dashboard)
    return {
        "generatedAt": dashboard.get("generatedAt"),
        "summary": summary,
        "timeline": timeline[:20],
        "alerts": notifications_snapshot,
        "simulations": simulations,
        "lastSuccessfulRefreshAt": dashboard.get("generatedAt"),
    }
