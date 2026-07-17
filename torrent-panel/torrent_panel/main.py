import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .qbittorrent import QBittorrentClient, QbitConfig, QbitError

logging.basicConfig(level=os.getenv("TORRENT_PANEL_LOG_LEVEL", "INFO"))
logger = logging.getLogger("torrent_panel")

STATIC_DIR = Path(__file__).parent / "static"
HASH_RE = re.compile(r"^[A-Fa-f0-9]{40}([A-Fa-f0-9]{24})?$")
PUBLIC_PREFIX = os.getenv("TORRENT_PANEL_PUBLIC_PREFIX", "/torrent-panel").rstrip("/")
CSRF_COOKIE = "torrent_panel_csrf"
CSRF_HEADER = "X-Torrent-Panel-CSRF"
MAX_RATE_KEYS = int(os.getenv("TORRENT_PANEL_RATE_LIMIT_KEYS", "2048"))
RATE_LIMIT_CALLS = int(os.getenv("TORRENT_PANEL_RATE_LIMIT_CALLS", "40"))
RATE_LIMIT_SECONDS = int(os.getenv("TORRENT_PANEL_RATE_LIMIT_SECONDS", "60"))
CSRF_TOKEN_TTL_SECONDS = int(os.getenv("TORRENT_PANEL_CSRF_TOKEN_TTL_SECONDS", "43200"))
MAX_CSRF_TOKENS = int(os.getenv("TORRENT_PANEL_CSRF_TOKEN_KEYS", "128"))
TRUSTED_PROXY_IPS = {
    item.strip()
    for item in os.getenv("TORRENT_PANEL_TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if item.strip()
}
ALLOWED_SAVE_PATHS = {
    item.strip()
    for item in os.getenv("TORRENT_PANEL_ALLOWED_SAVE_PATHS", "").split(",")
    if item.strip()
}


class TorrentAction(BaseModel):
    hash: str = Field(..., min_length=40, max_length=64)


class TorrentHashesAction(BaseModel):
    hashes: list[str] = Field(default_factory=list, min_length=1, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def accept_single_hash(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("hash") and not data.get("hashes"):
            return {**data, "hashes": [data["hash"]]}
        return data


class DeleteTorrent(TorrentHashesAction):
    deleteFiles: bool = False


class AddMagnet(BaseModel):
    magnet: str | None = Field(default=None, max_length=65535)
    magnets: list[str] = Field(default_factory=list, max_length=50)
    category: str = Field(default="", max_length=80)
    tags: str = Field(default="", max_length=200)
    paused: bool = False
    savePath: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def collect_magnets(self) -> "AddMagnet":
        collected: list[str] = []
        if self.magnet:
            collected.extend(self.magnet.splitlines())
        collected.extend(self.magnets)
        self.magnets = [item.strip() for item in collected if item.strip()]
        return self


class RateLimiter:
    def __init__(self, max_calls: int, period_seconds: int, max_keys: int) -> None:
        self._max_calls = max_calls
        self._period_seconds = period_seconds
        self._max_keys = max_keys
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._period_seconds:
            hits.popleft()
        if len(hits) >= self._max_calls:
            return False
        hits.append(now)
        self._cleanup(now)
        return True

    def _cleanup(self, now: float) -> None:
        if len(self._hits) <= self._max_keys:
            return
        for key in list(self._hits.keys()):
            hits = self._hits[key]
            while hits and now - hits[0] > self._period_seconds:
                hits.popleft()
            if not hits:
                del self._hits[key]
            if len(self._hits) <= self._max_keys:
                return
        for key in list(self._hits.keys())[: max(0, len(self._hits) - self._max_keys)]:
            del self._hits[key]


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
api_router = APIRouter()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/torrent-panel/static", StaticFiles(directory=STATIC_DIR), name="prefixed-static")
app.state.qbit = build_client()
app.state.csrf_tokens = {}
app.state.action_limiter = RateLimiter(
    max_calls=RATE_LIMIT_CALLS,
    period_seconds=RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state.qbit.close()


def validate_hash(torrent_hash: str) -> str:
    if not HASH_RE.fullmatch(torrent_hash):
        raise HTTPException(
            status_code=422,
            detail=error_detail("hash_invalid", "Hash torrent invalide.", "Réessayer"),
        )
    return torrent_hash.lower()


def validate_hashes(torrent_hashes: list[str]) -> list[str]:
    cleaned = [validate_hash(item) for item in torrent_hashes]
    if not cleaned:
        raise HTTPException(status_code=422, detail=error_detail("hash_invalid", "Aucun torrent sélectionné.", "Réessayer"))
    return cleaned


def validate_magnet(magnet: str) -> tuple[str | None, str | None]:
    candidate = magnet.strip()
    if not candidate.startswith("magnet:?"):
        return None, "Lien magnet invalide."
    if "xt=urn:btih:" not in candidate and "xt=urn:btmh:" not in candidate:
        return None, "Lien magnet sans identifiant torrent."
    return candidate, None


def error_detail(code: str, message: str, recovery: str) -> dict[str, str]:
    return {"code": code, "message": message, "recovery": recovery}


def client_key(request: Request) -> str:
    client_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and client_host in TRUSTED_PROXY_IPS:
        return forwarded.split(",", 1)[0].strip()
    return client_host


def require_action_guard(
    request: Request,
    x_torrent_panel_csrf: str | None = Header(default=None),
) -> None:
    if (
        not x_torrent_panel_csrf
        or not csrf_cookie_matches(request, x_torrent_panel_csrf)
        or not csrf_token_is_valid(request.app, x_torrent_panel_csrf)
    ):
        raise HTTPException(
            status_code=403,
            detail=error_detail("csrf_expired", "Session de protection expirée.", "Actualiser la session"),
        )

    if not request.app.state.action_limiter.allow(client_key(request)):
        raise HTTPException(
            status_code=429,
            detail=error_detail("rate_limited", "Trop d'actions en peu de temps.", "Réessayer"),
        )


def qbit_error_response(exc: QbitError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=error_detail(exc.code, exc.public_message, exc.recovery))


def cleanup_csrf_tokens(app_instance: FastAPI, now: float | None = None) -> None:
    current = now if now is not None else time.monotonic()
    tokens: dict[str, float] = app_instance.state.csrf_tokens
    for token, created_at in list(tokens.items()):
        if current - created_at > CSRF_TOKEN_TTL_SECONDS:
            del tokens[token]
    if len(tokens) <= MAX_CSRF_TOKENS:
        return
    for token, _created_at in sorted(tokens.items(), key=lambda item: item[1])[: len(tokens) - MAX_CSRF_TOKENS]:
        del tokens[token]


def csrf_token_is_valid(app_instance: FastAPI, token: str) -> bool:
    cleanup_csrf_tokens(app_instance)
    return token in app_instance.state.csrf_tokens


def csrf_cookie_matches(request: Request, token: str) -> bool:
    """Accept the token when any same-name cookie matches it.

    Browsers may retain cookies with the same name but different paths after a
    reverse-proxy or public-prefix change. ``request.cookies`` collapses those
    duplicates and can select the stale value, making session renewal fail
    forever even though the browser also sent the fresh cookie.
    """
    for item in request.headers.get("cookie", "").split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name == CSRF_COOKIE and secrets.compare_digest(value, token):
            return True
    return False


def set_csrf_cookie(request: Request, response: Response) -> str:
    is_https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    cleanup_csrf_tokens(app)
    token = secrets.token_urlsafe(32)
    app.state.csrf_tokens[token] = time.monotonic()
    response.set_cookie(
        CSRF_COOKIE,
        token,
        secure=is_https,
        httponly=False,
        samesite="strict",
        path=f"{PUBLIC_PREFIX}/" if PUBLIC_PREFIX else "/",
    )
    return token


@app.get("/")
@app.get("/torrent-panel")
@app.get("/torrent-panel/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
@app.get("/torrent-panel/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
@app.get("/torrent-panel/readyz")
async def readyz() -> dict[str, str]:
    try:
        await app.state.qbit.ready()
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "ready"}


@api_router.get("/session")
async def session(request: Request, response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"csrfToken": set_csrf_cookie(request, response)}


@api_router.get("/torrents")
async def torrents() -> dict[str, object]:
    try:
        return {"torrents": await app.state.qbit.torrents()}
    except QbitError as exc:
        raise qbit_error_response(exc) from exc


@api_router.post("/torrents/pause", dependencies=[Depends(require_action_guard)])
async def pause_torrent(payload: TorrentHashesAction) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await app.state.qbit.pause_many(hashes)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "paused", "count": len(hashes)}


@api_router.post("/torrents/resume", dependencies=[Depends(require_action_guard)])
async def resume_torrent(payload: TorrentHashesAction) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await app.state.qbit.resume_many(hashes)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "resumed", "count": len(hashes)}


@api_router.post("/torrents/delete", dependencies=[Depends(require_action_guard)])
async def delete_torrent(payload: DeleteTorrent) -> dict[str, object]:
    try:
        hashes = validate_hashes(payload.hashes)
        await app.state.qbit.delete_many(hashes, payload.deleteFiles)
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "deleted", "count": len(hashes)}


@api_router.post("/torrents/add", dependencies=[Depends(require_action_guard)])
async def add_torrent(payload: AddMagnet) -> dict[str, object]:
    if payload.savePath and payload.savePath not in ALLOWED_SAVE_PATHS:
        raise HTTPException(
            status_code=422,
            detail=error_detail("save_path_refused", "Chemin de sauvegarde non autorisé.", "Réessayer"),
        )

    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    for index, magnet in enumerate(payload.magnets, start=1):
        valid_magnet, reason = validate_magnet(magnet)
        if valid_magnet:
            accepted.append(valid_magnet)
        else:
            rejected.append({"line": str(index), "reason": reason or "Lien magnet invalide."})

    if not accepted:
        return {"status": "rejected", "accepted": 0, "rejected": rejected}

    try:
        await app.state.qbit.add_magnet(
            "\n".join(accepted),
            category=payload.category.strip(),
            tags=payload.tags.strip(),
            paused=payload.paused,
            save_path=payload.savePath.strip(),
        )
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "added", "accepted": len(accepted), "rejected": rejected}


app.include_router(api_router, prefix="/api")
if PUBLIC_PREFIX:
    app.include_router(api_router, prefix=f"{PUBLIC_PREFIX}/api")
