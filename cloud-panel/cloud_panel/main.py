from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_sys_path_root = Path(__file__).resolve().parents[2]
if str(_sys_path_root) not in sys.path:
    sys.path.insert(0, str(_sys_path_root))

from common import build_csp, RateLimiter  # noqa: E402
from common.monitoring import init_sentry  # noqa: E402
from common.performance import JobManager, SharedSectionHub, safe_error  # noqa: E402

from .config import (  # noqa: E402
    PUBLIC_PREFIX,
    MAX_RATE_KEYS,
    MOUNT_PATH,
    EVENTS_INTERVAL_SECONDS,
    EVENTS_KEEPALIVE_SECONDS,
    JOB_HEAVY_WORKERS,
    JOB_MAX_ENTRIES,
    JOB_MAX_QUEUED,
    JOB_STATE_PATH,
    LIVE_RATE_LIMIT_CALLS,
    LIVE_RATE_LIMIT_SECONDS,
    RATE_LIMIT_CALLS,
    RATE_LIMIT_SECONDS,
    READ_RATE_LIMIT_CALLS,
    READ_RATE_LIMIT_SECONDS,
    SHARE_RATE_LIMIT_CALLS,
    SHARE_RATE_LIMIT_SECONDS,
    STATIC_DIR,
    DOWNLOAD_STREAM_LIMIT,
    THUMBNAIL_CONCURRENCY,
    THUMBNAIL_RATE_LIMIT_CALLS,
    THUMBNAIL_RATE_LIMIT_SECONDS,
)
from .routes.events import router as events_router  # noqa: E402
from .routes.files import router as files_router  # noqa: E402
from .routes.favorites import router as favorites_router  # noqa: E402
from .routes.jobs import router as jobs_router  # noqa: E402
from .routes.share import download_share, router as share_router  # noqa: E402

from logging import basicConfig, getLogger  # noqa: E402

basicConfig(level=os.getenv("CLOUD_PANEL_LOG_LEVEL", "INFO"))
logger = getLogger("cloud_panel")


def _load_asset_manifest() -> tuple[str, frozenset[str]]:
    manifest_path = STATIC_DIR / "dist" / "asset-version.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = str(manifest.get("version") or "")
        files = manifest.get("files")
        if not re.fullmatch(r"[0-9a-f]{12}", version) or not isinstance(files, dict):
            raise ValueError("invalid asset manifest")
        for relative, expected in files.items():
            digest = hashlib.sha256((STATIC_DIR / relative).read_bytes()).hexdigest()
            if digest != expected:
                return "invalid", frozenset()
        allowed = {
            "dist/app.min.css",
            "dist/app.min.js",
            "fonts/Inter-Variable.woff2",
        }
        paths = frozenset(
            os.path.normpath(f"{PUBLIC_PREFIX}/static/{relative}")
            for relative in files
            if relative in allowed
        )
        if PUBLIC_PREFIX:
            paths |= frozenset(path.removeprefix(PUBLIC_PREFIX) for path in paths)
        return version, paths
    except (OSError, ValueError, TypeError):
        return "invalid", frozenset()


ASSET_VERSION, ASSET_PATHS = _load_asset_manifest()

init_sentry(os.getenv("SENTRY_DSN", ""), os.getenv("SENTRY_ENVIRONMENT", "production"))


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # A dedicated limiter keeps multi-gigabyte transfers from occupying the
    # shared interactive thread pool used by listings, searches and SSE.
    app_instance.state.download_limiter = anyio.CapacityLimiter(DOWNLOAD_STREAM_LIMIT)
    # Image decoding is bounded separately: a grid must never fill the pool.
    app_instance.state.thumbnail_encoder_limiter = anyio.CapacityLimiter(THUMBNAIL_CONCURRENCY)
    await app_instance.state.section_hub.start()
    try:
        yield
    finally:
        await app_instance.state.section_hub.close()
        await app_instance.state.job_manager.close()


app = FastAPI(
    title="Cloud Panel",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

_COMMON_CSS_DIR = Path(sys.modules["common"].__file__).resolve().parent / "css"
app.mount("/common/css", StaticFiles(directory=str(_COMMON_CSS_DIR)), name="common-css")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if PUBLIC_PREFIX:
    app.mount(f"{PUBLIC_PREFIX}/static", StaticFiles(directory=STATIC_DIR), name="prefixed-static")

app.state.csrf_tokens = {}
app.state.action_limiter = RateLimiter(
    max_calls=RATE_LIMIT_CALLS,
    period_seconds=RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)
app.state.share_limiter = RateLimiter(
    max_calls=SHARE_RATE_LIMIT_CALLS,
    period_seconds=SHARE_RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)
app.state.read_limiter = RateLimiter(
    max_calls=READ_RATE_LIMIT_CALLS,
    period_seconds=READ_RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)
app.state.thumbnail_limiter = RateLimiter(
    max_calls=THUMBNAIL_RATE_LIMIT_CALLS,
    period_seconds=THUMBNAIL_RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)
app.state.live_limiter = RateLimiter(
    max_calls=LIVE_RATE_LIMIT_CALLS,
    period_seconds=LIVE_RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)
app.state.last_csrf_cleanup = 0.0
app.state.mount_path = MOUNT_PATH
app.state.live_sections = OrderedDict()
app.state.job_manager = JobManager(
    max_workers=JOB_HEAVY_WORKERS,
    max_pending=JOB_MAX_QUEUED,
    retention=JOB_MAX_ENTRIES,
    queue_size=JOB_MAX_QUEUED,
    state_path=JOB_STATE_PATH,
    error_factory=lambda _exc: safe_error(
        code="job_failed",
        message="La tâche n’a pas pu aboutir.",
        recovery="Vérifier les données puis réessayer",
    ),
)
app.state.section_hub = SharedSectionHub(
    job_manager=app.state.job_manager,
    default_interval=EVENTS_INTERVAL_SECONDS,
    heartbeat_interval=EVENTS_KEEPALIVE_SECONDS,
)


_STATIC_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", build_csp()),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    (
        "Permissions-Policy",
        "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()",
    ),
)
_HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
_ASSET_VERSION_PATTERN = re.compile(r"[0-9a-f]{12}")
# The thumbnail route caches in the browser; every other API stays no-store.
_BROWSER_CACHE_EXEMPT_SUFFIX = "/api/files/thumbnail"


class SecurityHeadersMiddleware:
    """Pure ASGI security headers.

    ``BaseHTTPMiddleware`` used to wrap every response in an extra task and an
    in-memory stream, which copies the whole body (chunk by chunk) of a file
    download. This middleware only rewrites the ``http.response.start``
    message, so the transfer body travels straight from the route to uvicorn.
    """

    def __init__(self, app: ASGIApp, app_instance: FastAPI) -> None:
        self.app = app
        self.app_instance = app_instance

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = self.app_instance.state
        now = time.monotonic()
        last_cleanup = getattr(state, "last_csrf_cleanup", 0.0)
        if now - last_cleanup > 300:
            from .routes.csrf_guard import cleanup_csrf_tokens

            cleanup_csrf_tokens(self.app_instance)
            state.last_csrf_cleanup = now

        path = scope.get("path", "")
        secure = scope.get("scheme") == "https"
        cache_control = self._cache_control(scope, path)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _STATIC_SECURITY_HEADERS:
                    headers[name] = value
                if secure:
                    headers[_HSTS_HEADER[0]] = _HSTS_HEADER[1]
                if cache_control is not None:
                    if path.endswith(_BROWSER_CACHE_EXEMPT_SUFFIX):
                        headers.setdefault("Cache-Control", cache_control)
                    else:
                        headers["Cache-Control"] = cache_control
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _cache_control(scope: Scope, path: str) -> str | None:
        is_static_asset = "/static/dist/" in path or "/static/fonts/" in path
        if is_static_asset:
            is_built_asset = os.path.normpath(path) in ASSET_PATHS
            valid_version = _ASSET_VERSION_PATTERN.fullmatch(ASSET_VERSION or "") is not None
            if is_built_asset and valid_version and _query_value(scope, "v") == ASSET_VERSION:
                return "private, max-age=31536000, immutable"
            return "no-store"
        if "/api/" in path or path == "/" or path.endswith("/") or path.endswith("/config.js"):
            return "no-store"
        return None


def _query_value(scope: Scope, name: str) -> str | None:
    query = scope.get("query_string", b"")
    if not query:
        return None
    for pair in query.decode("latin-1").split("&"):
        key, _, value = pair.partition("=")
        if key == name:
            return value
    return None


app.add_middleware(SecurityHeadersMiddleware, app_instance=app)



@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config.js")
async def config_js() -> PlainTextResponse:
    return PlainTextResponse(
        "\n".join([
            "window.__CLOUD_PANEL_CONFIG__ = {",
            f'  publicPrefix: "{PUBLIC_PREFIX or ""}",',
            f'  cloudPanelPrefix: "{PUBLIC_PREFIX or ""}",',
            f'  torrentPanelPrefix: "{os.getenv("TORRENT_PANEL_PUBLIC_PREFIX", "/torrent-panel").rstrip("/")}",',
            f'  prowlarrPanelPrefix: "{os.getenv("PROWLARR_PANEL_PUBLIC_PREFIX", "/prowlarr-panel").rstrip("/")}",',
            f'  activityPrefix: "{os.getenv("TORRENT_PANEL_ACTIVITY_PUBLIC_PREFIX", "/activity").rstrip("/")}",',
            f'  storagePrefix: "{os.getenv("TORRENT_PANEL_STORAGE_PUBLIC_PREFIX", "/storage-panel").rstrip("/")}",',
            f'  mediaPrefix: "{os.getenv("TORRENT_PANEL_MEDIA_PUBLIC_PREFIX", "/media-panel").rstrip("/")}",',
            f'  healthPrefix: "{os.getenv("TORRENT_PANEL_HEALTH_PUBLIC_PREFIX", "/health").rstrip("/")}",',
            "};",
        ]),
        media_type="application/javascript",
    )


if PUBLIC_PREFIX:

    @app.get(PUBLIC_PREFIX)
    async def prefixed_index_redirect() -> FileResponse:
        from fastapi.responses import RedirectResponse
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
    return {"status": "ready"}


if PUBLIC_PREFIX:

    @app.get(f"{PUBLIC_PREFIX}/readyz")
    async def prefixed_readyz() -> dict[str, str]:
        return await readyz()


app.add_api_route("/download/{token}", download_share, methods=["GET"])
if PUBLIC_PREFIX:
    app.add_api_route(f"{PUBLIC_PREFIX}/download/{{token}}", download_share, methods=["GET"])


app.include_router(files_router, prefix="/api")
app.include_router(favorites_router, prefix="/api")
app.include_router(share_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(events_router, prefix="/api")
if PUBLIC_PREFIX:
    app.include_router(files_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(favorites_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(share_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(jobs_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(events_router, prefix=f"{PUBLIC_PREFIX}/api")
