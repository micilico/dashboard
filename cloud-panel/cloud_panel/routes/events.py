from __future__ import annotations

import hashlib
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from common import error_detail
from common.performance import SSE_HEADERS, SharedSectionHub, format_sse

from ..config import LIVE_SECTION_MAX_IDLE
from ..storage import canonical_directory, directory_summary

router = APIRouter()


def _topic(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:48]
    return f"files-{digest}"


async def _canonical_topic(request: Request, path: str) -> tuple[str, str]:
    try:
        canonical = await run_in_threadpool(canonical_directory, path)
    except (OSError, ValueError):
        raise HTTPException(
            status_code=403,
            detail=error_detail("path_error", "Chemin non autorisé ou introuvable.", "Vérifier le chemin"),
        ) from None
    return canonical, _topic(canonical)


async def _ensure_section(request: Request, canonical: str) -> str:
    hub: SharedSectionHub = request.app.state.section_hub
    registry: OrderedDict[str, str] = request.app.state.live_sections
    topic = _topic(canonical)
    if topic in registry:
        registry.move_to_end(topic)
        return topic

    collected_fingerprint: dict[str, object] = {}

    async def snapshot():
        summary = await run_in_threadpool(directory_summary, canonical)
        collected_fingerprint.update(
            path=summary.get("path"),
            fingerprint=summary.get("fingerprint"),
            total=summary.get("total"),
        )
        return summary

    # directory_summary carries operational timestamps that change on every
    # collector tick. Keep those in the latest snapshot for new subscribers,
    # while comparing only the actual directory contents for fan-out.
    hub.register(
        topic,
        snapshot,
        fingerprint_factory=lambda: collected_fingerprint,
    )
    registry[topic] = canonical

    # Canonical paths prevent alias duplication. This bounded LRU prevents an
    # unbounded registry as a user browses deeper trees. Active topics stay.
    while len(registry) > max(1, LIVE_SECTION_MAX_IDLE):
        for candidate in tuple(registry):
            if candidate == topic:
                continue
            if await hub.unregister(candidate):
                registry.pop(candidate, None)
                break
            registry.move_to_end(candidate)
        else:
            break
    return topic


def _check_live_rate(request: Request) -> None:
    limiter = getattr(request.app.state, "live_limiter", None)
    if limiter is not None and not limiter.allow(request.client.host if request.client else "unknown"):
        raise HTTPException(
            status_code=429,
            detail=error_detail("rate_limited", "Trop d’actualisations en peu de temps.", "Réessayer"),
        )


@router.get("/events")
async def events(request: Request, path: str = ""):
    _check_live_rate(request)
    _canonical, topic = await _canonical_topic(request, path)
    topic = await _ensure_section(request, _canonical)
    hub: SharedSectionHub = request.app.state.section_hub

    async def frames():
        async for frame in hub.stream([topic]):
            yield format_sse(frame)

    return StreamingResponse(frames(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/live")
async def live_snapshot(request: Request, path: str = ""):
    _check_live_rate(request)
    canonical, topic = await _canonical_topic(request, path)
    topic = await _ensure_section(request, canonical)
    hub: SharedSectionHub = request.app.state.section_hub
    # Polling is the fallback when SSE cannot be used. Always ask the shared
    # collector to refresh: its lock coalesces concurrent requests, while the
    # directory cache bounds repeated scans to the configured TTL. Returning a
    # retained frame here would leave polling clients stale forever when no
    # SSE subscriber is attached.
    frame = await hub.refresh(topic)
    envelope = frame.get("data") if isinstance(frame, dict) else None
    return {
        "sections": {topic: envelope if isinstance(envelope, dict) else {}},
        "jobs": await request.app.state.job_manager.snapshot(),
    }
