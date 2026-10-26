from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from common import error_detail
from common.performance import SSE_HEADERS, format_sse

from ..services.live import parse_topics

router = APIRouter()


async def _frames(request: Request, topics: tuple[str, ...]):
    hub = request.app.state.section_hub
    async for frame in hub.stream(topics):
        yield format_sse(frame)


@router.get("/events")
async def events(request: Request, topics: str | None = None) -> StreamingResponse:
    selected = parse_topics(topics)
    try:
        response = StreamingResponse(
            _frames(request, selected),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=422,
            detail=error_detail(
                "invalid_live_topic",
                "Une section temps réel demandée est inconnue.",
                "Recharger la page",
            ),
        ) from exc
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    return response
