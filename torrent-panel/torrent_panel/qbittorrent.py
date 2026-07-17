import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("torrent_panel.qbittorrent")


class QbitError(Exception):
    def __init__(self, status_code: int, public_message: str) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message


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

    async def close(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        if not self._config.url or not self._config.username or not self._config.password:
            raise QbitError(500, "Configuration qBittorrent incomplete.")

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
                raise QbitError(504, "qBittorrent ne repond pas assez vite.") from exc
            except httpx.RequestError as exc:
                logger.warning("qBittorrent login request failed: %s", exc.__class__.__name__)
                raise QbitError(502, "qBittorrent est injoignable.") from exc

            if response.status_code == 200 and response.text.strip() == "Ok.":
                self._authenticated = True
                return

            self._authenticated = False
            logger.warning("qBittorrent login rejected with status %s", response.status_code)
            raise QbitError(502, "Authentification qBittorrent refusee.")

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
            raise QbitError(504, "qBittorrent ne repond pas assez vite.") from exc
        except httpx.RequestError as exc:
            logger.warning("qBittorrent request failed on %s %s: %s", method, path, exc.__class__.__name__)
            raise QbitError(502, "qBittorrent est injoignable.") from exc

        if response.status_code in {401, 403} and retry_auth:
            self._authenticated = False
            await self._login()
            return await self._request(method, path, params=params, data=data, retry_auth=False)

        if response.status_code == 404:
            logger.warning("qBittorrent endpoint not found on %s %s", method, path)
            raise QbitError(404, "Action qBittorrent indisponible.")

        if response.status_code >= 400:
            logger.warning("qBittorrent returned %s on %s %s", response.status_code, method, path)
            raise QbitError(502, "qBittorrent a refuse l'action demandee.")

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
            }
            for item in torrents
            if isinstance(item, dict) and item.get("hash") and item.get("name")
        ]

    async def pause(self, torrent_hash: str) -> None:
        try:
            await self._request("POST", "/api/v2/torrents/pause", data={"hashes": torrent_hash})
        except QbitError as exc:
            if exc.status_code != 404:
                raise
            await self._request("POST", "/api/v2/torrents/stop", data={"hashes": torrent_hash})

    async def resume(self, torrent_hash: str) -> None:
        try:
            await self._request("POST", "/api/v2/torrents/resume", data={"hashes": torrent_hash})
        except QbitError as exc:
            if exc.status_code != 404:
                raise
            await self._request("POST", "/api/v2/torrents/start", data={"hashes": torrent_hash})

    async def delete(self, torrent_hash: str, delete_files: bool) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": torrent_hash, "deleteFiles": "true" if delete_files else "false"},
        )

    async def add_magnet(self, magnet: str) -> None:
        await self._request("POST", "/api/v2/torrents/add", data={"urls": magnet})
