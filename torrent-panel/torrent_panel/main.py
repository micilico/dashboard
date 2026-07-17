import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .qbittorrent import QBittorrentClient, QbitConfig, QbitError

logging.basicConfig(level=os.getenv("TORRENT_PANEL_LOG_LEVEL", "INFO"))
logger = logging.getLogger("torrent_panel")

STATIC_DIR = Path(__file__).parent / "static"
HASH_RE = re.compile(r"^[A-Fa-f0-9]{40}([A-Fa-f0-9]{24})?$")
CSRF_COOKIE = "torrent_panel_csrf"
CSRF_HEADER = "X-Torrent-Panel-CSRF"


class TorrentAction(BaseModel):
    hash: str = Field(..., min_length=40, max_length=64)


class DeleteTorrent(TorrentAction):
    deleteFiles: bool = False


class AddMagnet(BaseModel):
    magnet: str = Field(..., min_length=20, max_length=8192)


class RateLimiter:
    def __init__(self, max_calls: int, period_seconds: int) -> None:
        self._max_calls = max_calls
        self._period_seconds = period_seconds
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._period_seconds:
            hits.popleft()
        if len(hits) >= self._max_calls:
            return False
        hits.append(now)
        return True


def build_client() -> QBittorrentClient:
    return QBittorrentClient(
        QbitConfig(
            url=os.getenv("QBITTORRENT_URL", "http://127.0.0.1:16141"),
            username=os.getenv("QBITTORRENT_USERNAME", ""),
            password=os.getenv("QBITTORRENT_PASSWORD", ""),
            timeout_seconds=float(os.getenv("QBITTORRENT_TIMEOUT_SECONDS", "8")),
        )
    )


app = FastAPI(title="Torrent Panel", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.state.qbit = build_client()
app.state.csrf_token = secrets.token_urlsafe(32)
app.state.action_limiter = RateLimiter(max_calls=40, period_seconds=60)


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state.qbit.close()


def validate_hash(torrent_hash: str) -> str:
    if not HASH_RE.fullmatch(torrent_hash):
        raise HTTPException(status_code=422, detail="Hash torrent invalide.")
    return torrent_hash.lower()


def validate_magnet(magnet: str) -> str:
    candidate = magnet.strip()
    if not candidate.startswith("magnet:?"):
        raise HTTPException(status_code=422, detail="Lien magnet invalide.")
    if "xt=urn:btih:" not in candidate and "xt=urn:btmh:" not in candidate:
        raise HTTPException(status_code=422, detail="Lien magnet sans identifiant torrent.")
    return candidate


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def require_action_guard(
    request: Request,
    x_torrent_panel_csrf: str | None = Header(default=None),
) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    token = request.app.state.csrf_token
    if not cookie or not x_torrent_panel_csrf or cookie != token or x_torrent_panel_csrf != token:
        raise HTTPException(status_code=403, detail="Jeton de protection invalide.")

    if not request.app.state.action_limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="Trop d'actions en peu de temps. Reessayez dans une minute.")


def qbit_error_response(exc: QbitError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.public_message)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/session")
async def session(request: Request, response: Response) -> dict[str, str]:
    is_https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        CSRF_COOKIE,
        app.state.csrf_token,
        secure=is_https,
        httponly=False,
        samesite="strict",
        path="/",
    )
    return {"csrfToken": app.state.csrf_token}


@app.get("/api/torrents")
async def torrents() -> dict[str, object]:
    try:
        return {"torrents": await app.state.qbit.torrents()}
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@app.post("/api/torrents/pause", dependencies=[Depends(require_action_guard)])
async def pause_torrent(payload: TorrentAction) -> dict[str, str]:
    try:
        await app.state.qbit.pause(validate_hash(payload.hash))
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "paused"}


@app.post("/api/torrents/resume", dependencies=[Depends(require_action_guard)])
async def resume_torrent(payload: TorrentAction) -> dict[str, str]:
    try:
        await app.state.qbit.resume(validate_hash(payload.hash))
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "resumed"}


@app.post("/api/torrents/delete", dependencies=[Depends(require_action_guard)])
async def delete_torrent(payload: DeleteTorrent) -> dict[str, str]:
    try:
        await app.state.qbit.delete(validate_hash(payload.hash), payload.deleteFiles)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "deleted"}


@app.post("/api/torrents/add", dependencies=[Depends(require_action_guard)])
async def add_torrent(payload: AddMagnet) -> dict[str, str]:
    try:
        await app.state.qbit.add_magnet(validate_magnet(payload.magnet))
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "added"}
