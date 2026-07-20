import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

_sys_path_root = Path(__file__).resolve().parents[2]
if str(_sys_path_root) not in sys.path:
    sys.path.insert(0, str(_sys_path_root))

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from common import build_csp, client_key as _client_key, error_detail as _error_detail, RateLimiter
from common.monitoring import init_sentry
from common.csrf import (
    cleanup_csrf_tokens as _cleanup_csrf_tokens,
    csrf_cookie_matches as _csrf_cookie_matches,
    csrf_token_is_valid as _csrf_token_is_valid,
    set_csrf_cookie as _set_csrf_cookie,
)

from .prowlarr import ProwlarrClient, ProwlarrConfig, ProwlarrError

logging.basicConfig(level=os.getenv("PROWLARR_PANEL_LOG_LEVEL", "INFO"))
logger = logging.getLogger("prowlarr_panel")

STATIC_DIR = Path(__file__).parent / "static"
PUBLIC_PREFIX = os.getenv("PROWLARR_PANEL_PUBLIC_PREFIX", "/prowlarr-panel").rstrip("/")
TORRENT_PANEL_PUBLIC_PREFIX = os.getenv("TORRENT_PANEL_PUBLIC_PREFIX", "/torrent-panel").rstrip("/")
CSRF_COOKIE = "prowlarr_panel_csrf"
CSRF_HEADER = "X-Prowlarr-Panel-CSRF"
MAX_RATE_KEYS = int(os.getenv("PROWLARR_PANEL_RATE_LIMIT_KEYS", "2048"))
CSRF_TOKEN_TTL_SECONDS = int(os.getenv("PROWLARR_PANEL_CSRF_TOKEN_TTL_SECONDS", "43200"))
MAX_CSRF_TOKENS = int(os.getenv("PROWLARR_PANEL_CSRF_TOKEN_KEYS", "128"))
TRUSTED_PROXY_IPS = {
    item.strip()
    for item in os.getenv("PROWLARR_PANEL_TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if item.strip()
}


class SearchPayload(BaseModel):
    query: str = Field(default="", max_length=200)
    categories: list[int] = Field(default_factory=list, max_length=20)
    indexerIds: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("categories", "indexerIds")
    @classmethod
    def keep_positive_ids(cls, values: list[int]) -> list[int]:
        return [value for value in values if value > 0]


class IndexerAction(BaseModel):
    id: int = Field(..., ge=1)


class IndexerEnabled(IndexerAction):
    enabled: bool


class GrabPayload(BaseModel):
    releaseId: str = Field(..., min_length=16, max_length=200)
    title: str = Field(default="", max_length=500)


def parse_rate_limits(raw: str) -> dict[str, tuple[int, int]]:
    defaults = {"search": (20, 60), "test": (20, 60), "modify": (10, 60), "grab": (10, 60)}
    if not raw:
        return defaults
    parsed = dict(defaults)
    for part in raw.split(","):
        name, separator, value = part.strip().partition("=")
        count, slash, seconds = value.partition("/")
        if separator and slash and name in parsed and count.isdigit() and seconds.isdigit():
            parsed[name] = (int(count), int(seconds))
    return parsed


def build_client() -> ProwlarrClient:
    return ProwlarrClient(
        ProwlarrConfig(
            url=os.getenv("PROWLARR_URL", "http://127.0.0.1:16124/prowlarr"),
            api_key=os.getenv("PROWLARR_API_KEY", ""),
            timeout_seconds=float(os.getenv("PROWLARR_TIMEOUT_SECONDS", "8")),
            release_cache_ttl_seconds=int(os.getenv("PROWLARR_RELEASE_CACHE_TTL_SECONDS", "900")),
        )
    )


def error_detail(code: str, message: str, recovery: str) -> dict[str, str]:
    return _error_detail(code, message, recovery)


def prowlarr_error_response(exc: ProwlarrError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=error_detail(exc.code, exc.public_message, exc.recovery))


init_sentry(os.getenv("SENTRY_DSN", ""), os.getenv("SENTRY_ENVIRONMENT", "production"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await app.state.prowlarr.discover()
        logger.info("Prowlarr discovery completed")
    except ProwlarrError as exc:
        logger.warning("Prowlarr discovery pending: %s", exc.code)
    yield
    await app.state.prowlarr.close()


app = FastAPI(title="Prowlarr Panel", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
api_router = APIRouter()
_COMMON_CSS_DIR = Path(__file__).resolve().parents[2] / "common" / "css"
app.mount("/common/css", StaticFiles(directory=str(_COMMON_CSS_DIR)), name="common-css")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if PUBLIC_PREFIX:
    app.mount(f"{PUBLIC_PREFIX}/static", StaticFiles(directory=STATIC_DIR), name="prefixed-static")
app.state.prowlarr = build_client()
app.state.csrf_tokens = {}
app.state.limiters = {
    name: RateLimiter(max_calls=count, period_seconds=seconds, max_keys=MAX_RATE_KEYS)
    for name, (count, seconds) in parse_rate_limits(os.getenv("PROWLARR_PANEL_RATE_LIMIT", "")).items()
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = build_csp()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    if "/api/" in request.url.path:
        response.headers["Cache-Control"] = "no-store"
    return response


def cleanup_csrf_tokens(app_instance: FastAPI, now: float | None = None) -> None:
    _cleanup_csrf_tokens(app_instance, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS, now)


def csrf_token_is_valid(app_instance: FastAPI, token: str) -> bool:
    return _csrf_token_is_valid(app_instance, token, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS)


def csrf_cookie_matches(request: Request, token: str) -> bool:
    return _csrf_cookie_matches(request, token, CSRF_COOKIE)


def set_csrf_cookie(request: Request, response: Response) -> str:
    cookie_path = f"{PUBLIC_PREFIX}/" if PUBLIC_PREFIX else "/"
    return _set_csrf_cookie(app, request, response, CSRF_COOKIE, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS, cookie_path=cookie_path)


def client_key(request: Request) -> str:
    return _client_key(request, TRUSTED_PROXY_IPS)


def require_action_guard(kind: str):
    def guard(request: Request, x_prowlarr_panel_csrf: str | None = Header(default=None)) -> None:
        if (
            not x_prowlarr_panel_csrf
            or not csrf_cookie_matches(request, x_prowlarr_panel_csrf)
            or not csrf_token_is_valid(request.app, x_prowlarr_panel_csrf)
        ):
            raise HTTPException(
                status_code=403,
                detail=error_detail("csrf_expired", "Session de protection expirée.", "Actualiser la session"),
            )

        limiter = request.app.state.limiters[kind]
        if not limiter.allow(f"{kind}:{client_key(request)}"):
            raise HTTPException(
                status_code=429,
                detail=error_detail("rate_limited", "Trop d'actions en peu de temps.", "Réessayer"),
            )

    return guard


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config.js")
async def config_js() -> PlainTextResponse:
    return PlainTextResponse(
        "\n".join(
            [
                "window.__PROWLARR_PANEL_CONFIG__ = {",
                f'  publicPrefix: "{PUBLIC_PREFIX or ""}",',
                f'  torrentPanelPrefix: "{TORRENT_PANEL_PUBLIC_PREFIX or ""}",',
                "};",
            ]
        ),
        media_type="application/javascript",
    )


if PUBLIC_PREFIX:

    @app.get(PUBLIC_PREFIX)
    async def prefixed_index_redirect() -> RedirectResponse:
        return RedirectResponse(url=f"{PUBLIC_PREFIX}/", status_code=308)


    @app.get(f"{PUBLIC_PREFIX}/")
    async def prefixed_index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")


    @app.get(f"{PUBLIC_PREFIX}/config.js")
    async def prefixed_config_js() -> PlainTextResponse:
        return await config_js()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


if PUBLIC_PREFIX:

    @app.get(f"{PUBLIC_PREFIX}/healthz")
    async def prefixed_healthz() -> dict[str, str]:
        return await healthz()


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    try:
        await app.state.prowlarr.ready()
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc
    return {"status": "ready"}


if PUBLIC_PREFIX:

    @app.get(f"{PUBLIC_PREFIX}/readyz")
    async def prefixed_readyz() -> dict[str, str]:
        return await readyz()


@api_router.get("/session")
async def session(request: Request, response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"csrfToken": set_csrf_cookie(request, response)}


@api_router.get("/capabilities")
async def capabilities() -> dict[str, Any]:
    return {"capabilities": app.state.prowlarr.capabilities}


@api_router.post("/discover", dependencies=[Depends(require_action_guard("test"))])
async def discover() -> dict[str, Any]:
    try:
        return {"capabilities": await app.state.prowlarr.discover()}
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.get("/overview")
async def overview() -> dict[str, Any]:
    try:
        return await app.state.prowlarr.overview()
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.get("/indexers")
async def indexers() -> dict[str, Any]:
    try:
        return {"indexers": await app.state.prowlarr.indexers()}
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.post("/indexers/test", dependencies=[Depends(require_action_guard("test"))])
async def test_indexer(payload: IndexerAction) -> dict[str, Any]:
    try:
        return await app.state.prowlarr.test_indexer(payload.id)
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.post("/indexers/test-all", dependencies=[Depends(require_action_guard("test"))])
async def test_all_indexers() -> dict[str, Any]:
    try:
        return await app.state.prowlarr.test_indexer()
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.post("/indexers/enabled", dependencies=[Depends(require_action_guard("modify"))])
async def set_indexer_enabled(payload: IndexerEnabled) -> dict[str, Any]:
    try:
        return {"indexer": await app.state.prowlarr.set_indexer_enabled(payload.id, payload.enabled)}
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.post("/search", dependencies=[Depends(require_action_guard("search"))])
async def search(payload: SearchPayload) -> dict[str, Any]:
    query = payload.query.strip()
    if not query:
        return {"results": [], "partialFailures": []}
    try:
        return await app.state.prowlarr.search(query, payload.categories, payload.indexerIds)
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.post("/grab", dependencies=[Depends(require_action_guard("grab"))])
async def grab(payload: GrabPayload) -> dict[str, Any]:
    try:
        result = await app.state.prowlarr.grab(payload.releaseId)
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc
    return {**result, "title": payload.title}


@api_router.get("/applications")
async def applications() -> dict[str, Any]:
    try:
        return {"applications": await app.state.prowlarr.applications()}
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.get("/health")
async def prowlarr_health() -> dict[str, Any]:
    try:
        return {"alerts": await app.state.prowlarr.health()}
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.get("/history")
async def history() -> dict[str, Any]:
    try:
        return {"events": await app.state.prowlarr.history()}
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


app.include_router(api_router, prefix="/api")
if PUBLIC_PREFIX:
    app.include_router(api_router, prefix=f"{PUBLIC_PREFIX}/api")
