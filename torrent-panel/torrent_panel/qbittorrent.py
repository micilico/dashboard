import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("torrent_panel.qbittorrent")


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


@dataclass(frozen=True)
class QbitConfig:
    url: str
    username: str
    password: str
    timeout_seconds: float = 8.0


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
            logger.warning("qBittorrent endpoint not found on %s %s", method, path)
            raise QbitError(
                404,
                "Action qBittorrent indisponible.",
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

        return response

    async def torrents(self) -> list[dict[str, Any]]:
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
                "tracker": item.get("tracker", ""),
                "priority": item.get("priority", 0),
                "message": item.get("last_activity") or "",
            }
            for item in torrents
            if isinstance(item, dict) and item.get("hash") and item.get("name")
        ]

    async def ready(self) -> bool:
        await self._request("GET", "/api/v2/app/version")
        return True

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
