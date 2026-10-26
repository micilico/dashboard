import hashlib
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

_sys_path_root = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / "common" / "__init__.py").exists()),
    Path(__file__).resolve().parent,
)
if str(_sys_path_root) not in sys.path:
    sys.path.insert(0, str(_sys_path_root))

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from common import build_csp, client_key as _client_key, error_detail as _error_detail, require_csrf_token, RateLimiter
from common.monitoring import init_sentry
from common.performance import SharedSectionHub, safe_error
from common.csrf import (
    cleanup_csrf_tokens as _cleanup_csrf_tokens,
    csrf_cookie_matches as _csrf_cookie_matches,
    csrf_token_is_valid as _csrf_token_is_valid,
    set_csrf_cookie as _set_csrf_cookie,
)

from .prowlarr import ProwlarrClient, ProwlarrConfig, ProwlarrError, scrub

logging.basicConfig(level=os.getenv("PROWLARR_PANEL_LOG_LEVEL", "INFO"))
logger = logging.getLogger("prowlarr_panel")

STATIC_DIR = Path(__file__).parent / "static"
ASSET_FILES = {
    "/static/dist/app.min.css": STATIC_DIR / "dist" / "app.min.css",
    "/static/dist/app.min.js": STATIC_DIR / "dist" / "app.min.js",
}


def _content_version(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes() if path.exists() else b"")
    return digest.hexdigest()[:12]


ASSET_VERSIONS = {suffix: _content_version(path) for suffix, path in ASSET_FILES.items()}
PUBLIC_PREFIX = os.getenv("PROWLARR_PANEL_PUBLIC_PREFIX", "/prowlarr-panel").rstrip("/")
TORRENT_PANEL_PUBLIC_PREFIX = os.getenv("TORRENT_PANEL_PUBLIC_PREFIX", "/torrent-panel").rstrip("/")
CSRF_COOKIE = "prowlarr_panel_csrf"
CSRF_HEADER = "X-Prowlarr-Panel-CSRF"
MAX_RATE_KEYS = int(os.getenv("PROWLARR_PANEL_RATE_LIMIT_KEYS", "2048"))
CSRF_TOKEN_TTL_SECONDS = int(os.getenv("PROWLARR_PANEL_CSRF_TOKEN_TTL_SECONDS", "43200"))
MAX_CSRF_TOKENS = int(os.getenv("PROWLARR_PANEL_CSRF_TOKEN_KEYS", "128"))
LIVE_TOPICS = ("overview", "indexers", "applications", "health", "history")
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
            snapshot_cache_ttl_seconds=float(os.getenv("PROWLARR_SNAPSHOT_CACHE_TTL_SECONDS", "0.5")),
        )
    )


def error_detail(code: str, message: str, recovery: str) -> dict[str, str]:
    return _error_detail(code, message, recovery)


def prowlarr_error_response(exc: ProwlarrError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=error_detail(exc.code, exc.public_message, exc.recovery))


def _section_refreshed_at(name: str, payload: Any = None) -> str | None:
    return _section_refreshed_at_for(app.state.prowlarr, name, payload)


def _section_refreshed_at_for(client: Any, name: str, payload: Any = None) -> str | None:
    if isinstance(payload, dict) and payload.get("refreshedAt"):
        return str(payload["refreshedAt"])
    getter = getattr(client, "section_updated_at", None)
    return getter(name) if callable(getter) else None


async def _cached_section(name: str, force: bool = False) -> Any:
    return await _cached_section_for(app.state.prowlarr, name, force)


async def _cached_section_for(client: Any, name: str, force: bool = False) -> Any:
    method = getattr(client, f"cached_{name}", None)
    if callable(method):
        return await method(force=force)
    method = getattr(client, name, None)
    if callable(method):
        return await method()
    raise ProwlarrError(502, "Section Prowlarr indisponible.", code="prowlarr_section_unavailable")


def parse_live_topics(raw: str | None) -> tuple[str, ...]:
    requested = [item.strip() for item in str(raw or "").split(",") if item.strip()]
    return tuple(dict.fromkeys(item for item in requested if item in LIVE_TOPICS)) or ("overview", "indexers")


def build_live_hub(client: Any) -> SharedSectionHub:
    def public_error(exc: BaseException) -> dict[str, str]:
        if isinstance(exc, ProwlarrError):
            return {"code": exc.code, "message": exc.public_message, "recovery": exc.recovery}
        return safe_error(code="prowlarr_live_failed", message="Synchronisation Prowlarr indisponible.")

    latest_fingerprints: dict[str, dict[str, Any]] = {}

    async def section(name: str) -> dict[str, Any]:
        value = await _cached_section_for(client, name)
        payload_keys = {
            "indexers": "indexers",
            "applications": "applications",
            "health": "alerts",
            "history": "events",
        }
        payload = {
            payload_keys.get(name, name): value,
            "refreshedAt": _section_refreshed_at_for(client, name),
        }
        if name == "overview":
            payload = {**value, "refreshedAt": value.get("refreshedAt") or value.get("lastSuccessfulRefresh")}
        # refreshedAt can advance independently of the remote section's data
        # (for example, a cache read after a successful refresh). It belongs in
        # the delivered frame, but not in the change detector.
        latest_fingerprints[name] = {
            key: item for key, item in payload.items() if key != "refreshedAt"
        }
        return payload

    hub = SharedSectionHub(default_interval=0.5, error_factory=public_error)
    for topic in LIVE_TOPICS:
        hub.register(
            topic,
            lambda topic=topic: section(topic),
            fingerprint_factory=lambda topic=topic: latest_fingerprints.get(topic, {}),
        )
    return hub


init_sentry(os.getenv("SENTRY_DSN", ""), os.getenv("SENTRY_ENVIRONMENT", "production"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.prowlarr_live.start()
    try:
        await app.state.prowlarr.discover()
        logger.info("Prowlarr discovery completed")
    except ProwlarrError as exc:
        logger.warning("Prowlarr discovery pending: %s", exc.code)
    try:
        yield
    finally:
        await app.state.prowlarr_live.close()
        await app.state.prowlarr.close()


app = FastAPI(title="Prowlarr Panel", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
api_router = APIRouter()
_COMMON_CSS_DIR = Path(sys.modules["common"].__file__).resolve().parent / "css"
app.mount("/common/css", StaticFiles(directory=str(_COMMON_CSS_DIR)), name="common-css")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if PUBLIC_PREFIX:
    app.mount(f"{PUBLIC_PREFIX}/static", StaticFiles(directory=STATIC_DIR), name="prefixed-static")
app.state.prowlarr = build_client()
app.state.prowlarr_live = build_live_hub(app.state.prowlarr)
app.state.prowlarr_live_client = app.state.prowlarr
app.state.csrf_tokens = {}
app.state.limiters = {
    name: RateLimiter(max_calls=count, period_seconds=seconds, max_keys=MAX_RATE_KEYS)
    for name, (count, seconds) in parse_rate_limits(os.getenv("PROWLARR_PANEL_RATE_LIMIT", "")).items()
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.method != "GET":
        invalidate = getattr(request.app.state.prowlarr, "invalidate_snapshot", None)
        if invalidate is not None:
            invalidate()
    response.headers["Content-Security-Policy"] = build_csp()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    asset_suffix = next((suffix for suffix in ASSET_VERSIONS if request.url.path.endswith(suffix)), None)
    requested_version = request.query_params.get("v")
    if asset_suffix and requested_version == ASSET_VERSIONS[asset_suffix]:
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    elif (
        "/api/" in request.url.path
        or request.url.path == "/"
        or request.url.path.endswith("/")
        or request.url.path.endswith("/config.js")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


def _live_hub() -> SharedSectionHub:
    hub = getattr(app.state, "prowlarr_live", None)
    if hub is None or getattr(app.state, "prowlarr_live_client", None) is not app.state.prowlarr:
        hub = build_live_hub(app.state.prowlarr)
        app.state.prowlarr_live = hub
        app.state.prowlarr_live_client = app.state.prowlarr
    return hub


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
        require_csrf_token(
            request.app,
            request,
            x_prowlarr_panel_csrf,
            CSRF_COOKIE,
            CSRF_TOKEN_TTL_SECONDS,
            MAX_CSRF_TOKENS,
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
                f'  prowlarrPanelPrefix: "{PUBLIC_PREFIX or ""}",',
                f'  torrentPanelPrefix: "{TORRENT_PANEL_PUBLIC_PREFIX or ""}",',
                f'  cloudPanelPrefix: "{os.getenv("CLOUD_PANEL_PUBLIC_PREFIX", "/cloud-panel").rstrip("/")}",',
                f'  activityPrefix: "{os.getenv("TORRENT_PANEL_ACTIVITY_PUBLIC_PREFIX", "/activity").rstrip("/")}",',
                f'  storagePrefix: "{os.getenv("TORRENT_PANEL_STORAGE_PUBLIC_PREFIX", "/storage-panel").rstrip("/")}",',
                f'  mediaPrefix: "{os.getenv("TORRENT_PANEL_MEDIA_PUBLIC_PREFIX", "/media-panel").rstrip("/")}",',
                f'  healthPrefix: "{os.getenv("TORRENT_PANEL_HEALTH_PUBLIC_PREFIX", "/health").rstrip("/")}",',
                f'  statsPrefix: "{os.getenv("TORRENT_PANEL_STATS_PUBLIC_PREFIX", "/stats-panel").rstrip("/")}",',
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
async def overview(force: bool = False) -> dict[str, Any]:
    try:
        return await _cached_section("overview", force)
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.get("/indexers")
async def indexers(force: bool = False) -> dict[str, Any]:
    try:
        value = await _cached_section("indexers", force)
        return {"indexers": value, "refreshedAt": _section_refreshed_at("indexers")}
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
async def applications(force: bool = False) -> dict[str, Any]:
    try:
        value = await _cached_section("applications", force)
        return {"applications": value, "refreshedAt": _section_refreshed_at("applications")}
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.get("/health")
async def prowlarr_health(force: bool = False) -> dict[str, Any]:
    try:
        value = await _cached_section("health", force)
        return {"alerts": value, "refreshedAt": _section_refreshed_at("health")}
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.get("/history")
async def history(force: bool = False) -> dict[str, Any]:
    try:
        value = await _cached_section("history", force)
        return {"events": value, "refreshedAt": _section_refreshed_at("history")}
    except ProwlarrError as exc:
        raise prowlarr_error_response(exc) from exc


@api_router.get("/events")
async def events(request: Request, topics: str | None = None) -> StreamingResponse:
    requested_topics = parse_live_topics(topics)

    async def frames():
        async for raw_frame in _live_hub().stream(requested_topics):
            if await request.is_disconnected():
                break
            frame = scrub(raw_frame.get("data") or {})
            if frame.get("topic") and frame["topic"] not in requested_topics:
                continue
            event_name = str(raw_frame.get("event") or frame.get("type") or "message")
            sequence = raw_frame.get("id") or frame.get("sequence")
            lines = []
            if sequence is not None:
                lines.append(f"id: {int(sequence)}")
            lines.append(f"event: {event_name}")
            lines.append("data: " + json.dumps(frame, ensure_ascii=False, separators=(",", ":")))
            yield "\n".join(lines) + "\n\n"

    response = StreamingResponse(frames(), media_type="text/event-stream")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


app.include_router(api_router, prefix="/api")
if PUBLIC_PREFIX:
    app.include_router(api_router, prefix=f"{PUBLIC_PREFIX}/api")
