import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import QBIT_TORRENTS_CACHE_TTL_SECONDS

logger = logging.getLogger("torrent_panel.qbittorrent")


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


class AsyncTTLCache:
    """Small single-value async cache with concurrent-call coalescing."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = max(0.0, ttl_seconds)
        self._value: Any = None
        self._stored_at = 0.0
        self._task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()

    async def get_or_set(self, factory: Any, *, force: bool = False) -> Any:
        now = asyncio.get_running_loop().time()
        async with self._lock:
            if not force and self._value is not None and now - self._stored_at < self.ttl_seconds:
                return self._value
            if self._task is None:
                self._task = asyncio.create_task(factory())
            task = self._task
        try:
            value = await task
        except Exception:
            async with self._lock:
                if self._task is task:
                    self._task = None
            raise
        async with self._lock:
            if self._task is task:
                self._value = value
                self._stored_at = asyncio.get_running_loop().time()
                self._task = None
        return value

    def invalidate(self) -> None:
        self._stored_at = 0.0


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
        )
        self._login_lock = asyncio.Lock()
        self._authenticated = False
        self._torrents_cache = AsyncTTLCache(config.torrents_cache_ttl_seconds)

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

    async def _fetch_torrents(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/api/v2/torrents/info")
        try:
            torrents = response.json()
        except ValueError as exc:
            logger.warning("qBittorrent returned invalid JSON for torrents list")
            raise QbitError(502, "Reponse qBittorrent invalide.") from exc

        if not isinstance(torrents, list):
            raise QbitError(502, "Reponse qBittorrent invalide.")

        return [
            {
                "hash": item.get("hash"),
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
            for item in torrents
            if isinstance(item, dict) and item.get("hash") and item.get("name")
        ]

    async def torrents(self, *, force: bool = False) -> list[dict[str, Any]]:
        return await self._torrents_cache.get_or_set(self._fetch_torrents, force=force)

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
