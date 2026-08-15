"""Backend-only media identification using Jellyfin first, TMDB second."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from ..config import (
    JELLYFIN_API_KEY,
    JELLYFIN_API_URL,
    MEDIA_METADATA_CACHE_TTL_SECONDS,
    MEDIA_METADATA_TIMEOUT_SECONDS,
    TMDB_API_KEY,
    TMDB_API_URL,
)


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if token not in {"the", "a", "of"}}


def _score(query: str, candidate: str, year: str | None, candidate_year: Any) -> float:
    query_tokens = _tokens(query)
    candidate_tokens = _tokens(candidate)
    if not query_tokens or not candidate_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens) / len(query_tokens | candidate_tokens)
    if year and str(candidate_year or "") == str(year):
        overlap += 0.25
    return min(1.0, overlap)


class MediaMetadataResolver:
    """Cache and bound metadata lookups; never returns credentials or URLs."""

    def __init__(self, *, cache_ttl: float = MEDIA_METADATA_CACHE_TTL_SECONDS) -> None:
        self._cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._ttl = max(60.0, cache_ttl)
        self._limit = asyncio.Semaphore(4)

    async def resolve(self, title: str, kind: str, year: str | None = None) -> dict[str, Any]:
        key = (kind, title.casefold().strip(), str(year or ""))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return dict(cached[1])
        async with self._limit:
            result = await self._resolve_uncached(title, kind, year)
        self._cache[key] = (time.monotonic(), result)
        return dict(result)

    async def _resolve_uncached(self, title: str, kind: str, year: str | None) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(MEDIA_METADATA_TIMEOUT_SECONDS), trust_env=False) as client:
            if JELLYFIN_API_KEY:
                candidates.extend(await self._jellyfin(client, title, kind))
            if TMDB_API_KEY:
                candidates.extend(await self._tmdb(client, title, kind, year))
        ranked = sorted(
            ({**item, "score": _score(title, str(item.get("title") or ""), year, item.get("year"))} for item in candidates),
            key=lambda item: float(item["score"]),
            reverse=True,
        )
        if not ranked:
            return {"status": "heuristic", "confidence": "heuristic"}
        best = ranked[0]
        second_score = float(ranked[1]["score"]) if len(ranked) > 1 else 0.0
        confidence = "certain" if best["score"] >= 0.85 and best["score"] - second_score >= 0.1 else "ambiguous"
        return {
            "status": "resolved" if confidence == "certain" else "ambiguous",
            "confidence": confidence,
            "title": best.get("title", ""),
            "year": str(best.get("year") or "") or None,
            "source": best.get("source", ""),
        }

    async def _jellyfin(self, client: httpx.AsyncClient, title: str, kind: str) -> list[dict[str, Any]]:
        item_type = "Movie" if kind == "film" else "Series"
        try:
            response = await client.get(
                f"{JELLYFIN_API_URL.rstrip('/')}/Items",
                params={"SearchTerm": title, "IncludeItemTypes": item_type, "Recursive": "true", "Limit": "5"},
                headers={"X-Emby-Token": JELLYFIN_API_KEY},
            )
            if response.status_code >= 400:
                return []
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        return [
            {"title": item.get("Name", ""), "year": item.get("ProductionYear"), "source": "jellyfin"}
            for item in payload.get("Items", [])
            if isinstance(item, dict) and item.get("Name")
        ]

    async def _tmdb(self, client: httpx.AsyncClient, title: str, kind: str, year: str | None) -> list[dict[str, Any]]:
        endpoint = "movie" if kind == "film" else "tv"
        params = {"api_key": TMDB_API_KEY, "query": title, "language": "fr-FR"}
        if year and kind == "film":
            params["year"] = year
        try:
            response = await client.get(f"{TMDB_API_URL}/{endpoint}", params=params)
            if response.status_code >= 400:
                return []
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        items = payload.get("results", []) if isinstance(payload, dict) else []
        return [
            {
                "title": item.get("title") or item.get("name", ""),
                "year": str((item.get("release_date") or item.get("first_air_date") or "")[:4]) or None,
                "source": "tmdb",
            }
            for item in items
            if isinstance(item, dict) and (item.get("title") or item.get("name"))
        ]
