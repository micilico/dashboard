import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("prowlarr_panel.prowlarr")

SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|passkey|token|password|secret|cookie|authorization|username|user|rss|downloadurl|magnet|infohash|url)",
    re.IGNORECASE,
)
PRIVATE_URL_RE = re.compile(r"https?://\S+|magnet:\?\S+", re.IGNORECASE)


class ProwlarrError(Exception):
    def __init__(
        self,
        status_code: int,
        public_message: str,
        *,
        code: str = "prowlarr_error",
        recovery: str = "Réessayer",
    ) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message
        self.code = code
        self.recovery = recovery


@dataclass(frozen=True)
class ProwlarrConfig:
    url: str
    api_key: str
    timeout_seconds: float = 8.0


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = PRIVATE_URL_RE.sub("[URL masquée]", text)
    return text[:500]


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if SENSITIVE_KEY_RE.search(str(key)):
                continue
            cleaned[str(key)] = scrub(item)
        return cleaned
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return clean_text(value)
    return value


def first_present(item: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return default


def as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("records", "items", "data"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return []


def response_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        if isinstance(payload.get("message"), str):
            return clean_text(payload["message"])
        if isinstance(payload.get("errorMessage"), str):
            return clean_text(payload["errorMessage"])
        if isinstance(payload.get("errors"), list):
            messages = [
                clean_text(item.get("errorMessage") or item.get("message"))
                for item in payload["errors"]
                if isinstance(item, dict) and (item.get("errorMessage") or item.get("message"))
            ]
            return " ".join(item for item in messages if item)[:500]
    if isinstance(payload, list):
        messages = [
            clean_text(item.get("errorMessage") or item.get("message"))
            for item in payload
            if isinstance(item, dict) and (item.get("errorMessage") or item.get("message"))
        ]
        return " ".join(item for item in messages if item)[:500]
    return ""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ProwlarrClient:
    def __init__(self, config: ProwlarrConfig) -> None:
        self._config = config
        self._api_root = "/api/v1"
        self._client = httpx.AsyncClient(
            base_url=config.url.rstrip("/"),
            timeout=httpx.Timeout(config.timeout_seconds),
            headers={
                "User-Agent": "prowlarr-panel/1.0",
                "X-Api-Key": config.api_key,
                "Accept": "application/json",
            },
        )
        self._capabilities: dict[str, Any] = {
            "detected": False,
            "version": None,
            "basePath": urlparse(config.url).path.rstrip("/") or "/",
            "endpoints": {},
            "limits": [],
            "lastDiscoveryAt": None,
        }

    @property
    def capabilities(self) -> dict[str, Any]:
        return dict(self._capabilities)

    async def close(self) -> None:
        await self._client.aclose()

    def _request_error(self, exc: httpx.RequestError) -> ProwlarrError:
        host = urlparse(self._config.url).hostname or ""
        message = "Prowlarr est injoignable."
        code = "prowlarr_unreachable"
        recovery = "Réessayer"
        if host in {"127.0.0.1", "localhost", "host.docker.internal"}:
            message = "Tunnel SSH ou port Prowlarr indisponible."
            code = "ssh_tunnel_unavailable"
            recovery = "Vérifier le tunnel"
        logger.warning("Prowlarr request error: %s", exc.__class__.__name__)
        return ProwlarrError(502, message, code=code, recovery=recovery)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: Any | None = None,
        json: Any | None = None,
        acceptable: set[int] | None = None,
    ) -> httpx.Response:
        if not self._config.url or not self._config.api_key:
            raise ProwlarrError(
                500,
                "Configuration Prowlarr incomplete.",
                code="prowlarr_config_missing",
                recovery="Vérifier la configuration",
            )

        try:
            response = await self._client.request(method, path, params=params, data=data, json=json)
        except httpx.TimeoutException as exc:
            logger.warning("Prowlarr request timed out on %s %s", method, path)
            raise ProwlarrError(
                504,
                "Prowlarr ne répond pas assez vite.",
                code="prowlarr_timeout",
                recovery="Réessayer",
            ) from exc
        except httpx.RequestError as exc:
            raise self._request_error(exc) from exc

        if acceptable and response.status_code in acceptable:
            return response
        if response.status_code in {401, 403}:
            logger.warning("Prowlarr authentication refused with status %s", response.status_code)
            raise ProwlarrError(
                502,
                "Clé API Prowlarr refusée.",
                code="prowlarr_auth_refused",
                recovery="Vérifier la clé API",
            )
        if response.status_code == 404:
            logger.info("Prowlarr endpoint unavailable on %s %s", method, path)
            raise ProwlarrError(
                404,
                "Action Prowlarr indisponible sur cette version.",
                code="prowlarr_action_unavailable",
                recovery="Utiliser l'interface Prowlarr native",
            )
        if response.status_code == 400:
            message = response_error_message(response)
            logger.warning("Prowlarr rejected request with validation error on %s %s", method, path)
            raise ProwlarrError(
                502,
                message or "Prowlarr a refusé la demande.",
                code="prowlarr_validation_refused",
                recovery="Vérifier l'indexer dans Prowlarr",
            )
        if response.status_code >= 400:
            logger.warning("Prowlarr returned %s on %s %s", response.status_code, method, path)
            raise ProwlarrError(
                502,
                "Prowlarr a refusé la demande.",
                code="prowlarr_action_refused",
                recovery="Réessayer",
            )
        return response

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            logger.warning("Prowlarr returned invalid JSON on %s %s", method, path)
            raise ProwlarrError(502, "Réponse Prowlarr invalide.", code="prowlarr_invalid_response") from exc

    async def _endpoint_exists(self, path: str) -> bool:
        try:
            await self._request("GET", path, acceptable={200, 400, 405})
            return True
        except ProwlarrError as exc:
            if exc.status_code == 404:
                return False
            raise

    async def discover(self) -> dict[str, Any]:
        status = await self.system_status()
        endpoints: dict[str, bool] = {}
        for name, path in {
            "systemStatus": f"{self._api_root}/system/status",
            "indexer": f"{self._api_root}/indexer",
            "indexerTest": f"{self._api_root}/indexer/test",
            "indexerTestAll": f"{self._api_root}/indexer/testall",
            "search": f"{self._api_root}/search",
            "searchRelease": f"{self._api_root}/search/release",
            "download": f"{self._api_root}/download",
            "application": f"{self._api_root}/applications",
            "health": f"{self._api_root}/health",
            "history": f"{self._api_root}/history",
        }.items():
            endpoints[name] = await self._endpoint_exists(path)
        self._capabilities = {
            "detected": True,
            "version": status.get("version"),
            "basePath": status.get("urlBase") or urlparse(self._config.url).path.rstrip("/") or "/",
            "endpoints": endpoints,
            "limits": [
                "Les endpoints sont validés au démarrage contre l'instance réelle.",
                "Les URLs privées, passkeys, tokens et champs de configuration sensibles sont supprimés des réponses.",
                "L'activation/désactivation est proposée seulement si la mise à jour d'indexer est acceptée par l'API.",
                "L'envoi vers qBittorrent utilise l'action native de téléchargement Prowlarr quand elle est disponible.",
            ],
            "lastDiscoveryAt": now_iso(),
        }
        return self.capabilities

    async def system_status(self) -> dict[str, Any]:
        payload = await self._json("GET", f"{self._api_root}/system/status")
        return scrub(payload) if isinstance(payload, dict) else {}

    async def ready(self) -> bool:
        await self.system_status()
        return True

    async def indexers(self) -> list[dict[str, Any]]:
        payload = await self._json("GET", f"{self._api_root}/indexer")
        return [self._map_indexer(item) for item in as_list(payload)]

    async def applications(self) -> list[dict[str, Any]]:
        for path in (f"{self._api_root}/applications", f"{self._api_root}/application"):
            try:
                payload = await self._json("GET", path)
                return [self._map_application(item) for item in as_list(payload)]
            except ProwlarrError as exc:
                if exc.status_code != 404:
                    raise
        return []

    async def health(self) -> list[dict[str, Any]]:
        payload = await self._json("GET", f"{self._api_root}/health")
        return [self._map_health(item) for item in as_list(payload)]

    async def history(self, page_size: int = 25) -> list[dict[str, Any]]:
        payload = await self._json("GET", f"{self._api_root}/history", params={"pageSize": page_size, "sortKey": "date", "sortDirection": "descending"})
        return [self._map_history(item) for item in as_list(payload)]

    async def overview(self) -> dict[str, Any]:
        status = await self.system_status()
        indexers = await self.indexers()
        applications = await self.applications()
        health = await self.health()
        active = [item for item in indexers if item["enabled"]]
        errored = [item for item in indexers if item["health"] == "error"]
        return {
            "connection": "ready",
            "version": status.get("version") or self._capabilities.get("version") or "inconnue",
            "indexersTotal": len(indexers),
            "indexersActive": len(active),
            "indexersDisabled": len(indexers) - len(active),
            "indexersError": len(errored),
            "applicationsTotal": len(applications),
            "systemWarnings": len(health),
            "lastSuccessfulRefresh": now_iso(),
            "capabilities": self.capabilities,
        }

    async def test_indexer(self, indexer_id: int | None = None) -> dict[str, Any]:
        if indexer_id is None:
            response = await self._request("POST", f"{self._api_root}/indexer/testall", acceptable={200, 202})
            payload = response.json() if response.content else {}
            return {"status": "accepted", "result": scrub(payload)}

        current = await self._json("GET", f"{self._api_root}/indexer/{indexer_id}")
        if not isinstance(current, dict):
            raise ProwlarrError(502, "Réponse Prowlarr invalide.", code="prowlarr_invalid_response")
        response = await self._request("POST", f"{self._api_root}/indexer/test", json=current, acceptable={200, 202})
        payload = response.json() if response.content else {}
        return {"status": "accepted", "result": scrub(payload)}

    async def set_indexer_enabled(self, indexer_id: int, enabled: bool) -> dict[str, Any]:
        current = await self._json("GET", f"{self._api_root}/indexer/{indexer_id}")
        if not isinstance(current, dict):
            raise ProwlarrError(502, "Réponse Prowlarr invalide.", code="prowlarr_invalid_response")
        current["enable"] = enabled
        current["enabled"] = enabled
        payload = await self._json("PUT", f"{self._api_root}/indexer/{indexer_id}", json=current)
        return self._map_indexer(payload if isinstance(payload, dict) else current)

    async def search(self, query: str, categories: list[int], indexer_ids: list[int]) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query}
        if categories:
            params["categories"] = ",".join(str(item) for item in categories)
        if indexer_ids:
            params["indexerIds"] = ",".join(str(item) for item in indexer_ids)
        payload = await self._json("GET", f"{self._api_root}/search", params=params)
        releases = [self._map_release(item) for item in as_list(payload)]
        return {"results": releases, "partialFailures": []}

    async def grab(self, guid: str, indexer_id: int | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"guid": guid}
        if indexer_id is not None:
            payload["indexerId"] = indexer_id
        response = await self._request("POST", f"{self._api_root}/download", json=payload, acceptable={200, 201, 202})
        body = response.json() if response.content else {}
        return {"status": "sent", "release": scrub(body)}

    def _map_indexer(self, item: dict[str, Any]) -> dict[str, Any]:
        fields = item.get("fields") if isinstance(item.get("fields"), list) else []
        field_errors = [
            clean_text(field.get("errorMessage") or field.get("message"))
            for field in fields
            if isinstance(field, dict) and (field.get("errorMessage") or field.get("message"))
        ]
        enabled = bool(first_present(item, "enable", "enabled", default=False))
        status = first_present(item, "status", "health", default="")
        health = "error" if field_errors or str(status).lower() in {"error", "unavailable", "failed"} else ("disabled" if not enabled else "ok")
        tags = first_present(item, "tags", default=[])
        return {
            "id": item.get("id"),
            "name": clean_text(item.get("name")),
            "protocol": clean_text(first_present(item, "protocol", "protocolName", default="inconnu")),
            "enabled": enabled,
            "priority": first_present(item, "priority", "appProfileId", default=0),
            "tags": tags if isinstance(tags, list) else [],
            "categories": scrub(first_present(item, "categories", default=[])),
            "health": health,
            "lastTest": clean_text(first_present(item, "lastTest", "lastRssSyncReleaseInfo", default="")),
            "error": field_errors[0] if field_errors else clean_text(first_present(item, "errorMessage", "message", default="")),
            "stats": scrub(first_present(item, "statistics", "stats", default={})),
        }

    def _map_application(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "name": clean_text(item.get("name")),
            "type": clean_text(first_present(item, "implementationName", "implementation", default="application")),
            "enabled": bool(first_present(item, "enable", "enabled", default=False)),
            "lastTest": clean_text(first_present(item, "lastTest", "status", default="")),
            "syncLevel": clean_text(first_present(item, "syncLevel", "syncCategories", default="")),
            "tags": first_present(item, "tags", default=[]) if isinstance(first_present(item, "tags", default=[]), list) else [],
        }

    def _map_health(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": clean_text(first_present(item, "type", "source", default="warning")),
            "source": clean_text(first_present(item, "source", "wikiUrl", default="Prowlarr")),
            "message": clean_text(first_present(item, "message", "title", default="Alerte système")),
            "date": clean_text(first_present(item, "date", "time", default="")),
        }

    def _map_history(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "eventType": clean_text(first_present(item, "eventType", "eventTypeName", default="event")),
            "indexer": clean_text(first_present(item, "indexer", "indexerName", default="")),
            "result": clean_text(first_present(item, "successful", "status", default="")),
            "date": clean_text(first_present(item, "date", "time", default="")),
            "title": clean_text(first_present(item, "sourceTitle", "title", default="")),
        }

    def _map_release(self, item: dict[str, Any]) -> dict[str, Any]:
        indexer_id = first_present(item, "indexerId", "indexerID", default=None)
        guid = clean_text(first_present(item, "guid", "downloadGuid", "infoHash", default=""))
        opaque_id = f"{indexer_id or 'any'}:{guid}"
        return {
            "id": opaque_id,
            "guid": guid,
            "indexerId": indexer_id,
            "title": clean_text(first_present(item, "title", "releaseTitle", default="Sans titre")),
            "indexer": clean_text(first_present(item, "indexer", "indexerName", default="")),
            "category": clean_text(first_present(item, "category", "categoryName", default="")),
            "size": first_present(item, "size", default=0),
            "age": first_present(item, "age", "ageHours", "publishDate", default=""),
            "seeders": first_present(item, "seeders", "seedCount", default=0),
            "leechers": first_present(item, "leechers", "leechCount", default=0),
            "protocol": clean_text(first_present(item, "protocol", default="")),
            "downloadFactor": first_present(item, "downloadVolumeFactor", "downloadFactor", default=None),
            "uploadFactor": first_present(item, "uploadVolumeFactor", "uploadFactor", default=None),
            "freeleech": bool(first_present(item, "freeleech", "isFreeleech", default=False)),
        }
