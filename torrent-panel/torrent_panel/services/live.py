from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from common.performance import JobManager, SharedSectionHub
from .monitoring import now_iso

from ..config import (
    LIVE_DASHBOARD_INTERVAL_SECONDS,
    LIVE_SLOW_INTERVAL_SECONDS,
    LIVE_STATUS_INTERVAL_SECONDS,
    LIVE_TORRENTS_INTERVAL_SECONDS,
)
from .monitoring import (
    activity_snapshot,
    dashboard_snapshot,
    health_snapshot,
    jellyfin_snapshot,
    stats_snapshot,
    storage_snapshot,
)
from .relink import relink_status

VALID_TOPICS = frozenset(
    {
        "torrents",
        "dashboard",
        "storage",
        "activity",
        "health",
        "media",
        "stats",
        "relink",
    }
)
DEFAULT_TOPICS = ("torrents", "dashboard", "relink")

_VOLATILE_FINGERPRINT_KEYS = frozenset(
    {
        "collectedAt",
        "observedAt",
        "generatedAt",
        "checkedAt",
        "lastSuccessfulCheckAt",
        "lastSuccessfulRefreshAt",
    }
)


def _stable_fingerprint(value: Any) -> Any:
    """Ignore collection timestamps so unchanged snapshots stay quiet on SSE."""
    if isinstance(value, dict):
        return {
            key: _stable_fingerprint(item)
            for key, item in value.items()
            if key not in _VOLATILE_FINGERPRINT_KEYS
        }
    if isinstance(value, list):
        return [_stable_fingerprint(item) for item in value]
    return value


def parse_topics(raw: str | None) -> tuple[str, ...]:
    requested = [item.strip().lower() for item in str(raw or "").split(",") if item.strip()]
    topics = tuple(dict.fromkeys(item for item in requested if item in VALID_TOPICS))
    return topics or DEFAULT_TOPICS


def build_live_hub(app: FastAPI, job_manager: JobManager) -> SharedSectionHub:
    hub = SharedSectionHub(
        job_manager=job_manager,
        default_interval=LIVE_TORRENTS_INTERVAL_SECONDS,
        heartbeat_interval=15.0,
    )
    fingerprints: dict[str, Any] = {}

    async def torrents() -> dict[str, Any]:
        result = {
            "torrents": await app.state.qbit.torrents(),
            "observedAt": now_iso(),
        }
        fingerprints["torrents"] = _stable_fingerprint(result)
        return result

    async def dashboard() -> dict[str, Any]:
        result = await dashboard_snapshot(app)
        fingerprints["dashboard"] = _stable_fingerprint(result)
        return result

    async def storage() -> dict[str, Any]:
        result = await storage_snapshot(app)
        fingerprints["storage"] = _stable_fingerprint(result)
        return result

    async def activity() -> dict[str, Any]:
        result = await activity_snapshot(app)
        fingerprints["activity"] = _stable_fingerprint(result)
        return result

    async def health() -> dict[str, Any]:
        result = await health_snapshot(app)
        fingerprints["health"] = _stable_fingerprint(result)
        return result

    async def media() -> dict[str, Any]:
        result = await jellyfin_snapshot()
        fingerprints["media"] = _stable_fingerprint(result)
        return result

    async def stats() -> dict[str, Any]:
        result = await stats_snapshot(app)
        fingerprints["stats"] = _stable_fingerprint(result)
        return result

    async def relink() -> dict[str, int]:
        return await relink_status(app.state.qbit)

    hub.register(
        "torrents", torrents, interval=LIVE_TORRENTS_INTERVAL_SECONDS,
        fingerprint_factory=lambda: fingerprints.get("torrents"),
    )
    hub.register(
        "dashboard", dashboard, interval=LIVE_DASHBOARD_INTERVAL_SECONDS,
        fingerprint_factory=lambda: fingerprints.get("dashboard"),
    )
    hub.register(
        "storage", storage, interval=LIVE_STATUS_INTERVAL_SECONDS,
        fingerprint_factory=lambda: fingerprints.get("storage"),
    )
    hub.register(
        "activity", activity, interval=LIVE_STATUS_INTERVAL_SECONDS,
        fingerprint_factory=lambda: fingerprints.get("activity"),
    )
    hub.register(
        "health", health, interval=LIVE_STATUS_INTERVAL_SECONDS,
        fingerprint_factory=lambda: fingerprints.get("health"),
    )
    hub.register(
        "media", media, interval=LIVE_SLOW_INTERVAL_SECONDS,
        fingerprint_factory=lambda: fingerprints.get("media"),
    )
    hub.register(
        "stats", stats, interval=LIVE_SLOW_INTERVAL_SECONDS,
        fingerprint_factory=lambda: fingerprints.get("stats"),
    )
    hub.register("relink", relink, interval=LIVE_STATUS_INTERVAL_SECONDS)
    return hub
