"""Torrent Panel – FastAPI application entry point."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

_sys_path_root = Path(__file__).resolve().parents[2]
if str(_sys_path_root) not in sys.path:
    sys.path.insert(0, str(_sys_path_root))

from common import build_csp, RateLimiter  # noqa: E402
from common.monitoring import init_sentry  # noqa: E402
from common.csrf import cleanup_csrf_tokens as _common_cleanup  # noqa: E402
from common.csrf import csrf_cookie_matches, csrf_token_is_valid  # noqa: E402

from .config import (  # noqa: E402
    ACTIVITY_PUBLIC_PREFIX,
    AUTOMATION_RULES_STATE_PATH,
    CONSOLE_PREFIXES,
    CSRF_COOKIE,
    CSRF_TOKEN_TTL_SECONDS,
    HEALTH_PUBLIC_PREFIX,
    MAX_CSRF_TOKENS,
    MAX_RATE_KEYS,
    MEDIA_PUBLIC_PREFIX,
    NOTIFICATION_STATE_PATH,
    PROWLARR_PANEL_PUBLIC_PREFIX,
    PUBLIC_PREFIX,
    RATE_LIMIT_CALLS,
    RATE_LIMIT_SECONDS,
    STATIC_DIR,
    STORAGE_PUBLIC_PREFIX,
    TRUSTED_PROXY_IPS,
)
from .qbittorrent import QbitConfig, QBittorrentClient, QbitError  # noqa: E402
from .routes.automations import router as automations_router  # noqa: E402
from .routes.dashboard import router as dashboard_router  # noqa: E402
from .routes.media_automation import router as media_automation_router  # noqa: E402
from .routes.notifications import router as notifications_router  # noqa: E402
from .routes.torrents import (  # noqa: E402
    qbit_error_response,
    router as torrents_router,
    validate_hash,
    validate_magnet,
)
from .services.automations import AutomationRuleStore  # noqa: E402
from .services.media_automation import (  # noqa: E402
    build_media_automation_config,
    MediaAutomationConfig,
    MediaAutomationError,
    MediaAutomationManager,
)
from .services.notifications import NotificationCenter  # noqa: E402

from logging import basicConfig, getLogger  # noqa: E402

basicConfig(level=os.getenv("TORRENT_PANEL_LOG_LEVEL", "INFO"))
logger = getLogger("torrent_panel")


def build_client() -> QBittorrentClient:
    return QBittorrentClient(
        QbitConfig(
            url=os.getenv("QBITTORRENT_URL", "http://127.0.0.1:16141"),
            username=os.getenv("QBITTORRENT_USERNAME", ""),
            password=os.getenv("QBITTORRENT_PASSWORD", ""),
            timeout_seconds=float(os.getenv("QBITTORRENT_TIMEOUT_SECONDS", "8")),
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.media_automation.start()
    yield
    await app.state.media_automation.stop()
    await app.state.qbit.close()


init_sentry(os.getenv("SENTRY_DSN", ""), os.getenv("SENTRY_ENVIRONMENT", "production"))

app = FastAPI(title="Torrent Panel", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

_COMMON_CSS_DIR = Path(__file__).resolve().parents[1] / "common" / "css"
app.mount("/common/css", StaticFiles(directory=str(_COMMON_CSS_DIR)), name="common-css")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if PUBLIC_PREFIX:
    app.mount(f"{PUBLIC_PREFIX}/static", StaticFiles(directory=STATIC_DIR), name="prefixed-static")
for prefix in CONSOLE_PREFIXES:
    if prefix:
        app.mount(f"{prefix}/static", StaticFiles(directory=STATIC_DIR), name=f"{prefix.strip('/').replace('-', '_')}_static")

app.state.qbit = build_client()
app.state.media_automation = MediaAutomationManager(app.state.qbit, build_media_automation_config())
app.state.notifications = NotificationCenter(NOTIFICATION_STATE_PATH)
app.state.automation_rules = AutomationRuleStore(AUTOMATION_RULES_STATE_PATH)
app.state.csrf_tokens = {}
app.state.action_limiter = RateLimiter(
    max_calls=RATE_LIMIT_CALLS,
    period_seconds=RATE_LIMIT_SECONDS,
    max_keys=MAX_RATE_KEYS,
)
app.state.service_checks = {}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = build_csp()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response


def error_detail(code: str, message: str, recovery: str) -> dict[str, str]:
    from common import error_detail as _ed
    return _ed(code, message, recovery)


def cleanup_csrf_tokens(app_instance, now=None):
    return _common_cleanup(app_instance, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS, now)


def client_key(request: Request) -> str:
    from common.csrf import client_key as _ck
    return _ck(request, TRUSTED_PROXY_IPS)


def require_action_guard(request: Request, x_torrent_panel_csrf: str | None = None):
    from fastapi import Header
    if x_torrent_panel_csrf is None:
        x_torrent_panel_csrf = request.headers.get("X-Torrent-Panel-CSRF")
    if (
        not x_torrent_panel_csrf
        or not csrf_cookie_matches(request, x_torrent_panel_csrf, CSRF_COOKIE)
        or not csrf_token_is_valid(request.app, x_torrent_panel_csrf, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS)
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


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config.js")
async def config_js() -> PlainTextResponse:
    return PlainTextResponse(
        "\n".join(
            [
                "window.__TORRENT_PANEL_CONFIG__ = {",
                f'  publicPrefix: "{PUBLIC_PREFIX or ""}",',
                f'  prowlarrPanelPrefix: "{PROWLARR_PANEL_PUBLIC_PREFIX or ""}",',
                "};",
            ]
        ),
        media_type="application/javascript",
    )


def _console_config_js(section: str, prefix: str) -> PlainTextResponse:
    return PlainTextResponse(
        "\n".join(
            [
                "window.__DASHBOARD_CONSOLE_CONFIG__ = {",
                f'  section: "{section}",',
                f'  publicPrefix: "{prefix}",',
                f'  apiPrefix: "{prefix}/api",',
                f'  torrentPanelPrefix: "{PUBLIC_PREFIX or ""}",',
                f'  prowlarrPanelPrefix: "{PROWLARR_PANEL_PUBLIC_PREFIX or ""}",',
                f'  activityPrefix: "{ACTIVITY_PUBLIC_PREFIX or ""}",',
                f'  storagePrefix: "{STORAGE_PUBLIC_PREFIX or ""}",',
                f'  mediaPrefix: "{MEDIA_PUBLIC_PREFIX or ""}",',
                f'  healthPrefix: "{HEALTH_PUBLIC_PREFIX or ""}",',
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


def register_console_page(prefix: str, section: str, filename: str) -> None:
    if not prefix:
        return

    @app.get(prefix)
    async def _console_redirect(_prefix: str = prefix) -> RedirectResponse:
        return RedirectResponse(url=f"{_prefix}/", status_code=308)

    @app.get(f"{prefix}/")
    async def _console_index(_filename: str = filename) -> FileResponse:
        return FileResponse(STATIC_DIR / _filename)

    @app.get(f"{prefix}/config.js")
    async def _console_config(_section: str = section, _prefix: str = prefix) -> PlainTextResponse:
        return _console_config_js(_section, _prefix)


register_console_page(ACTIVITY_PUBLIC_PREFIX, "activity", "activity.html")
register_console_page(STORAGE_PUBLIC_PREFIX, "storage", "storage.html")
register_console_page(MEDIA_PUBLIC_PREFIX, "media", "media.html")
register_console_page(HEALTH_PUBLIC_PREFIX, "health", "health.html")


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
        await app.state.qbit.ready()
    except QbitError as exc:
        raise qbit_error_response(exc) from exc
    return {"status": "ready"}


if PUBLIC_PREFIX:

    @app.get(f"{PUBLIC_PREFIX}/readyz")
    async def prefixed_readyz() -> dict[str, str]:
        return await readyz()


# Include all routers under /api and prefixed variants
app.include_router(torrents_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(media_automation_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(automations_router, prefix="/api")

if PUBLIC_PREFIX:
    app.include_router(torrents_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(dashboard_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(media_automation_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(notifications_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(automations_router, prefix=f"{PUBLIC_PREFIX}/api")

for prefix in CONSOLE_PREFIXES:
    if prefix:
        app.include_router(dashboard_router, prefix=f"{prefix}/api")
        app.include_router(notifications_router, prefix=f"{prefix}/api")
        app.include_router(media_automation_router, prefix=f"{prefix}/api")
