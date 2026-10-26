from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import QBIT_TORRENTS_CACHE_TTL_SECONDS

logger = logging.getLogger("torrent_panel.qbittorrent")

# One client instance owns one bounded keep-alive pool that every route, job and
# automation shares. Reusing sockets removes the repeated TCP setup of a busy
# dashboard, while the cap keeps a slow qBittorrent from accumulating sockets.
QBIT_MAX_CONNECTIONS = int(os.getenv("TORRENT_PANEL_QBIT_MAX_CONNECTIONS", "16"))
QBIT_MAX_KEEPALIVE_CONNECTIONS = int(
    os.getenv("TORRENT_PANEL_QBIT_MAX_KEEPALIVE_CONNECTIONS", "8")
)

# qBittorrent answers an unknown Web API route with 404; the incremental sync
# endpoint is missing on older builds, so that answer selects the fallback.
SYNC_UNSUPPORTED_STATUS = 404
INVALID_RESPONSE_CODE = "qbit_response_invalid"


def public_tracker_host(url: str) -> str:
    """Return only the non-secret tracker endpoint for browser payloads."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.port and parsed.port not in {80, 443}:
            return f"{host}:{parsed.port}"
        return host
    except ValueError:
        return ""


def _sanitize_torrent(item: Any, torrent_hash: str | None = None) -> dict[str, Any] | None:
    """Map one qBittorrent torrent payload to the public dashboard shape.

    The list endpoint and the incremental sync endpoint both describe the same
    torrents, so every field is projected here once and both paths return
    strictly identical objects.
    """
    if not isinstance(item, dict):
        return None
    item_hash = str(item.get("hash") or torrent_hash or "")
    if not item_hash or not item.get("name"):
        return None
    return {
        "hash": item_hash,
        "name": item.get("name"),
        "state": item.get("state"),
        "progress": item.get("progress", 0),
        "downloadSpeed": item.get("dlspeed", 0),
        "uploadSpeed": item.get("upspeed", 0),
        "ratio": item.get("ratio", 0),
        "size": item.get("size", 0),
        "downloaded": item.get("downloaded", 0),
        "uploaded": item.get("uploaded", 0),
        "remaining": item.get("amount_left", 0),
        "eta": item.get("eta", 0),
        "addedOn": item.get("added_on", 0),
        "completionOn": item.get("completion_on", 0),
        "seeders": item.get("num_seeds", 0),
        "leechers": item.get("num_leechs", item.get("num_leeches", 0)),
        "availability": item.get("availability", 0),
        "category": item.get("category", ""),
        "tags": item.get("tags", ""),
        "savePath": item.get("save_path", ""),
        "contentPath": item.get("content_path", ""),
        "tracker": public_tracker_host(str(item.get("tracker") or "")),
        "priority": item.get("priority", 0),
        "message": item.get("last_activity") or "",
        "downloadLimit": item.get("dl_limit", -1),
        "uploadLimit": item.get("up_limit", -1),
        "sequentialDownload": item.get("seq_dl", False),
        "isPrivate": item.get("is_private", False),
    }


class QbitError(Exception):
    def __init__(
        self,
        status_code: int,
        public_message: str,
        *,
        code: str = "qbit_error",
        recovery: str = "Réessayer",
    ) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message
        self.code = code
        self.recovery = recovery


def _invalid_sync_response() -> QbitError:
    """Error that tells the client to fall back to the complete torrents list."""
    return QbitError(
        502,
        "Reponse qBittorrent invalide.",
        code=INVALID_RESPONSE_CODE,
        recovery="Reessayer",
    )


@dataclass(frozen=True)
class _TorrentSnapshot:
    """Raw qBittorrent fields keyed by hash, plus their sync cursor.

    A maindata payload sends partial raw fields keyed by torrent hash; values
    do not reliably include the hash or fields already known. Keeping the raw
    map lets one delta merge into the prior state before any public projection.
    """

    raw_by_hash: dict[str, dict[str, Any]]
    rid: int | None = None

    def as_list(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for torrent_hash, raw in self.raw_by_hash.items():
            sanitized = _sanitize_torrent(raw, torrent_hash)
            if sanitized is not None:
                result.append(sanitized)
        return result


class AsyncTTLCache:
    """Single-value async cache with coalescing and safe invalidation.

    Concurrent readers share one in-flight read, so a burst of callers never
    turns into a burst of upstream requests. Invalidation bumps a generation
    instead of cancelling work other callers are already awaiting: the running
    read is delivered to its waiters but can no longer be stored, which keeps a
    mutation from resurrecting a snapshot that was captured before it.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = max(0.0, ttl_seconds)
        self._value: Any = None
        self._has_value = False
        self._stored_at = 0.0
        self._generation = 0
        self._task: asyncio.Task[Any] | None = None
        self._task_generation = -1
        self._lock = asyncio.Lock()

    async def get_or_set(self, factory: Any, *, force: bool = False) -> Any:
        loop = asyncio.get_running_loop()
        async with self._lock:
            if not force and self._has_value and loop.time() - self._stored_at < self.ttl_seconds:
                return self._value
            if self._task is None or self._task_generation != self._generation:
                # A read started before the last invalidation must not satisfy
                # this caller, so it is replaced instead of awaited.
                self._task = asyncio.create_task(factory())
                self._task_generation = self._generation
            task = self._task
            task_generation = self._task_generation
        try:
            # Shielded so cancelling this caller never cancels the shared read.
            value = await asyncio.shield(task)
        except asyncio.CancelledError:
            async with self._lock:
                if self._task is task and task.cancelled():
                    self._task = None
                    self._task_generation = -1
            raise
        except Exception:
            async with self._lock:
                if self._task is task:
                    self._task = None
                    self._task_generation = -1
            raise
        async with self._lock:
            if self._task is task:
                self._task = None
                self._task_generation = -1
            if task_generation == self._generation:
                self._value = value
                self._has_value = True
                self._stored_at = loop.time()
        return value

    def invalidate(self) -> None:
        """Expire the stored value without disturbing an in-flight read."""
        self._generation += 1
        self._value = None
        self._has_value = False
        self._stored_at = 0.0

    def peek(self) -> Any:
        """Return the stored value, expired or not, without refreshing it."""
        return self._value if self._has_value else None

    async def aclose(self) -> None:
        async with self._lock:
            task = self._task
            self._task = None
            self._task_generation = -1
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@dataclass(frozen=True)


class QbitConfig:
    url: str
    username: str
    password: str
    timeout_seconds: float = 8.0
    torrents_cache_ttl_seconds: float = QBIT_TORRENTS_CACHE_TTL_SECONDS


class QBittorrentClient:
    def __init__(self, config: QbitConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.url.rstrip("/"),
            timeout=httpx.Timeout(config.timeout_seconds),
            headers={"User-Agent": "torrent-panel/1.0"},
            limits=httpx.Limits(
                max_connections=QBIT_MAX_CONNECTIONS,
                max_keepalive_connections=QBIT_MAX_KEEPALIVE_CONNECTIONS,
            ),
        )
        self._login_lock = asyncio.Lock()
        self._authenticated = False
        self._torrents_cache = AsyncTTLCache(config.torrents_cache_ttl_seconds)
        # Cleared for the lifetime of the client after the first unanswered
        # probe, so an older qBittorrent is not probed again on every refresh.
        self._sync_supported = True

    def _request_error(self, exc: httpx.RequestError) -> QbitError:
        host = urlparse(self._config.url).hostname or ""
        message = "qBittorrent est injoignable."
        code = "qbit_unreachable"
        recovery = "Réessayer"
        if host in {"127.0.0.1", "localhost", "host.docker.internal"}:
            message = "Tunnel SSH ou port qBittorrent indisponible."
            code = "ssh_tunnel_unavailable"
            recovery = "Vérifier le tunnel"
        logger.warning("qBittorrent request error: %s", exc.__class__.__name__)
        return QbitError(502, message, code=code, recovery=recovery)

    async def close(self) -> None:
        await self._torrents_cache.aclose()
        await self._client.aclose()

    async def _login(self) -> None:
        if not self._config.url or not self._config.username or not self._config.password:
            raise QbitError(
                500,
                "Configuration qBittorrent incomplete.",
                code="qbit_config_missing",
                recovery="Vérifier le tunnel",
            )

        async with self._login_lock:
            if self._authenticated:
                # Another caller refreshed the session while this one waited.
                return
            try:
                response = await self._client.post(
                    "/api/v2/auth/login",
                    data={
                        "username": self._config.username,
                        "password": self._config.password,
                    },
                )
            except httpx.TimeoutException as exc:
                logger.warning("qBittorrent login timed out: %s", exc.__class__.__name__)
                raise QbitError(
                    504,
                    "qBittorrent ne répond pas assez vite.",
                    code="qbit_timeout",
                    recovery="Réessayer",
                ) from exc
            except httpx.RequestError as exc:
                raise self._request_error(exc) from exc

            if response.status_code == 200 and response.text.strip() == "Ok.":
                self._authenticated = True
                return

            self._authenticated = False
            logger.warning("qBittorrent login rejected with status %s", response.status_code)
            raise QbitError(
                502,
                "Authentification qBittorrent refusée.",
                code="qbit_auth_refused",
                recovery="Actualiser la session",
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> httpx.Response:
        if not self._authenticated:
            await self._login()

        try:
            response = await self._client.request(method, path, params=params, data=data)
        except httpx.TimeoutException as exc:
            logger.warning("qBittorrent request timed out on %s %s", method, path)
            raise QbitError(
                504,
                "qBittorrent ne répond pas assez vite.",
                code="qbit_timeout",
                recovery="Réessayer",
            ) from exc
        except httpx.RequestError as exc:
            raise self._request_error(exc) from exc

        if response.status_code in {401, 403} and retry_auth:
            self._authenticated = False
            await self._login()
            return await self._request(method, path, params=params, data=data, retry_auth=False)

        if response.status_code == 404:
            logger.warning("qBittorrent 404 on %s %s", method, path)
            raise QbitError(
                404,
                "Torrent inconnu ou action qBittorrent indisponible.",
                code="qbit_action_unavailable",
                recovery="Réessayer",
            )

        if response.status_code >= 400:
            logger.warning("qBittorrent returned %s on %s %s", response.status_code, method, path)
            raise QbitError(
                502,
                "qBittorrent a refusé l'action demandée.",
                code="qbit_action_refused",
                recovery="Réessayer",
            )

        if method.upper() != "GET":
            self.invalidate_torrents()
        return response

    async def _fetch_torrents_incremental(self) -> _TorrentSnapshot:
        """Read the torrents delta that follows the snapshot currently cached."""
        previous = self._torrents_cache.peek()
        requested_rid = previous.rid if previous is not None and previous.rid else None
        params = {"rid": requested_rid} if requested_rid else None
        response = await self._request("GET", "/api/v2/sync/maindata", params=params)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            logger.warning("qBittorrent returned invalid JSON for sync/maindata")
            raise _invalid_sync_response()

        changed = payload.get("torrents")
        removed = payload.get("torrents_removed")
        full_update = payload.get("full_update") is True
        has_changed = isinstance(changed, dict)
        has_removed = isinstance(removed, list)

        rid = payload.get("rid")
        if isinstance(rid, bool) or not isinstance(rid, int) or rid < 0:
            rid = None

        if not has_changed and not has_removed and not full_update:
            # qBittorrent answers an already current rid with an empty payload.
            if previous is None:
                raise _invalid_sync_response()
            if rid is None:
                return previous
            return _TorrentSnapshot(raw_by_hash=previous.raw_by_hash, rid=rid)

        if rid is None or (full_update and not has_changed) or (not full_update and requested_rid is None):
            # Without a cursor, the full map, or a rid this client owns, the
            # payload cannot be anchored; the caller reads the complete list.
            raise _invalid_sync_response()

        raw_by_hash: dict[str, dict[str, Any]] = {}
        if not full_update and previous is not None:
            raw_by_hash.update(
                (torrent_hash, dict(raw))
                for torrent_hash, raw in previous.raw_by_hash.items()
            )
        if has_changed:
            for raw_hash, partial in changed.items():
                torrent_hash = str(raw_hash or "").lower()
                if not torrent_hash or not isinstance(partial, dict):
                    continue
                merged = dict(raw_by_hash.get(torrent_hash, {}))
                merged.update(partial)
                # The map key, not a possibly absent value field, is canonical.
                merged["hash"] = torrent_hash
                raw_by_hash[torrent_hash] = merged
        if has_removed:
            for removed_hash in removed:
                raw_by_hash.pop(str(removed_hash).lower(), None)
        return _TorrentSnapshot(raw_by_hash=raw_by_hash, rid=rid)

    async def _fetch_torrents_full(self) -> _TorrentSnapshot:
        """Read the complete list; used when the incremental endpoint is unusable."""
        response = await self._request("GET", "/api/v2/torrents/info")
        try:
            torrents = response.json()
        except ValueError as exc:
            logger.warning("qBittorrent returned invalid JSON for torrents list")
            raise QbitError(502, "Reponse qBittorrent invalide.") from exc

        if not isinstance(torrents, list):
            raise QbitError(502, "Reponse qBittorrent invalide.")

        raw_by_hash: dict[str, dict[str, Any]] = {}
        for item in torrents:
            if not isinstance(item, dict):
                continue
            torrent_hash = str(item.get("hash") or "").lower()
            if torrent_hash:
                raw_by_hash[torrent_hash] = {**item, "hash": torrent_hash}
        # The list endpoint carries no sync cursor, so the next read restarts
        # from a full update instead of guessing a rid.
        return _TorrentSnapshot(raw_by_hash=raw_by_hash, rid=None)

    async def _fetch_torrents(self) -> _TorrentSnapshot:
        if self._sync_supported:
            try:
                return await self._fetch_torrents_incremental()
            except QbitError as exc:
                if exc.status_code == SYNC_UNSUPPORTED_STATUS:
                    # Older Web APIs have no sync endpoint: stop probing.
                    logger.info("qBittorrent sync/maindata unavailable; using torrents/info")
                    self._sync_supported = False
                elif exc.code == INVALID_RESPONSE_CODE:
                    logger.warning("qBittorrent sync/maindata payload unusable; using torrents/info")
                else:
                    raise
        return await self._fetch_torrents_full()

    async def torrents(self, *, force: bool = False) -> list[dict[str, Any]]:
        snapshot = await self._torrents_cache.get_or_set(self._fetch_torrents, force=force)
        return snapshot.as_list()

    def invalidate_torrents(self) -> None:
        self._torrents_cache.invalidate()

    async def ready(self) -> bool:
        await self._request("GET", "/api/v2/app/version")
        return True

    async def webapi_version(self) -> str:
        response = await self._request("GET", "/api/v2/app/webapiVersion")
        return response.text.strip()

    async def pause(self, torrent_hash: str) -> None:
        await self.pause_many([torrent_hash])

    async def pause_many(self, torrent_hashes: list[str]) -> None:
        try:
            await self._request("POST", "/api/v2/torrents/pause", data={"hashes": "|".join(torrent_hashes)})
        except QbitError as exc:
            if exc.status_code != 404:
                raise
            await self._request("POST", "/api/v2/torrents/stop", data={"hashes": "|".join(torrent_hashes)})

    async def resume(self, torrent_hash: str) -> None:
        await self.resume_many([torrent_hash])

    async def resume_many(self, torrent_hashes: list[str]) -> None:
        try:
            await self._request("POST", "/api/v2/torrents/resume", data={"hashes": "|".join(torrent_hashes)})
        except QbitError as exc:
            if exc.status_code != 404:
                raise
            await self._request("POST", "/api/v2/torrents/start", data={"hashes": "|".join(torrent_hashes)})

    async def set_force_start(self, torrent_hash: str, enabled: bool) -> None:
        await self.set_force_start_many([torrent_hash], enabled)

    async def set_force_start_many(self, torrent_hashes: list[str], enabled: bool) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/setForceStart",
            data={
                "hashes": "|".join(torrent_hashes),
                "value": "true" if enabled else "false",
            },
        )

    async def delete(self, torrent_hash: str, delete_files: bool) -> None:
        await self.delete_many([torrent_hash], delete_files)

    async def delete_many(self, torrent_hashes: list[str], delete_files: bool) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": "|".join(torrent_hashes), "deleteFiles": "true" if delete_files else "false"},
        )

    async def add_magnet(
        self,
        magnet: str,
        *,
        category: str = "",
        tags: str = "",
        paused: bool = False,
        save_path: str = "",
    ) -> None:
        data = {"urls": magnet}
        if category:
            data["category"] = category
        if tags:
            data["tags"] = tags
        if paused:
            data["paused"] = "true"
        if save_path:
            data["savepath"] = save_path
        await self._request("POST", "/api/v2/torrents/add", data=data)

    async def recheck_many(self, torrent_hashes: list[str]) -> None:
        await self._request("POST", "/api/v2/torrents/recheck", data={"hashes": "|".join(torrent_hashes)})

    async def reannounce_many(self, torrent_hashes: list[str]) -> None:
        await self._request("POST", "/api/v2/torrents/reannounce", data={"hashes": "|".join(torrent_hashes)})

    async def set_category_many(self, torrent_hashes: list[str], category: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/setCategory",
            data={"hashes": "|".join(torrent_hashes), "category": category},
        )

    async def add_tags_many(self, torrent_hashes: list[str], tags: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/addTags",
            data={"hashes": "|".join(torrent_hashes), "tags": tags},
        )

    async def categories(self) -> dict[str, dict[str, Any]]:
        response = await self._request("GET", "/api/v2/torrents/categories")
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("qBittorrent returned invalid JSON for categories")
            raise QbitError(502, "Reponse qBittorrent invalide.") from exc
        if not isinstance(payload, dict):
            raise QbitError(502, "Reponse qBittorrent invalide.")
        return {str(name): item for name, item in payload.items() if isinstance(item, dict)}

    async def set_location_many(self, torrent_hashes: list[str], location: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/setLocation",
            data={"hashes": "|".join(torrent_hashes), "location": location},
        )

    async def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        """Rename or move one torrent-owned file while keeping qBittorrent's metadata aligned."""
        await self._request(
            "POST",
            "/api/v2/torrents/renameFile",
            data={"hash": torrent_hash, "oldPath": old_path, "newPath": new_path},
        )

    async def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        """Rename a torrent-owned folder through qBittorrent's Web API."""
        await self._request(
            "POST",
            "/api/v2/torrents/renameFolder",
            data={"hash": torrent_hash, "oldPath": old_path, "newPath": new_path},
        )

    async def set_content_layout_many(self, torrent_hashes: list[str], layout: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/setContentLayout",
            data={"hashes": "|".join(torrent_hashes), "layout": layout},
        )

    async def set_download_limit_many(self, torrent_hashes: list[str], limit_bytes: int) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/setDownloadLimit",
            data={"hashes": "|".join(torrent_hashes), "limit": str(limit_bytes)},
        )

    async def set_upload_limit_many(self, torrent_hashes: list[str], limit_bytes: int) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/setUploadLimit",
            data={"hashes": "|".join(torrent_hashes), "limit": str(limit_bytes)},
        )

    async def set_sequential_download_many(self, torrent_hashes: list[str], enabled: bool) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/toggleSequentialDownload",
            data={"hashes": "|".join(torrent_hashes)},
        )
        if not enabled:
            await self._request(
                "POST",
                "/api/v2/torrents/toggleSequentialDownload",
                data={"hashes": "|".join(torrent_hashes)},
            )

    async def trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        response = await self._request("GET", "/api/v2/torrents/trackers", params={"hash": torrent_hash})
        try:
            payload = response.json()
        except ValueError as exc:
            raise QbitError(502, "Réponse qBittorrent invalide.") from exc
        if not isinstance(payload, list):
            raise QbitError(502, "Réponse qBittorrent invalide.")
        return [item for item in payload if isinstance(item, dict)]

    async def add_tracker(self, torrent_hash: str, tracker_url: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/addTrackers",
            data={"hash": torrent_hash, "urls": tracker_url},
        )

    async def files(self, torrent_hash: str) -> list[dict[str, Any]]:
        response = await self._request("GET", "/api/v2/torrents/files", params={"hash": torrent_hash})
        try:
            payload = response.json()
        except ValueError as exc:
            raise QbitError(502, "Réponse qBittorrent invalide.") from exc
        if not isinstance(payload, list):
            raise QbitError(502, "Réponse qBittorrent invalide.")
        return [item for item in payload if isinstance(item, dict)]
