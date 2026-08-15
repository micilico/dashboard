"""Torrent Panel – FastAPI application entry point."""

from __future__ import annotations

import os
import sys
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

_sys_path_root = Path(__file__).resolve().parents[2]
if str(_sys_path_root) not in sys.path:
    sys.path.insert(0, str(_sys_path_root))

from common import build_csp, RateLimiter  # noqa: E402
from common.monitoring import init_sentry  # noqa: E402
from common.csrf import csrf_cookie_matches, csrf_token_is_valid  # noqa: E402

from .config import (  # noqa: E402
    ACTIVITY_PUBLIC_PREFIX,
    AUTO_RELINK_ENABLED,
    AUTO_RELINK_INTERVAL_SECONDS,
    AUTOMATION_RULES_STATE_PATH,
    CONSOLE_PREFIXES,
    HEALTH_PUBLIC_PREFIX,
    MAX_RATE_KEYS,
    MEDIA_PUBLIC_PREFIX,
    NOTIFICATION_STATE_PATH,
    LIBRARY_ORGANIZER_RUNS_PATH,
    ORGANIZER_RESUME_POLL_SECONDS,
    ORGANIZER_RESUME_STATE_PATH,
    PROWLARR_PANEL_PUBLIC_PREFIX,
    PUBLIC_PREFIX,
    RATE_LIMIT_CALLS,
    RATE_LIMIT_SECONDS,
    RATIO_ALERT_THRESHOLD,
    RATIO_MONITOR_STATE_PATH,
    STATIC_DIR,
    STATS_HISTORY_DAYS,
    STATS_PUBLIC_PREFIX,
    STATS_STATE_PATH,
    STORAGE_PUBLIC_PREFIX,
    TRACKER_STATS_STATE_PATH,
)
from .qbittorrent import QbitConfig, QBittorrentClient, QbitError  # noqa: E402
from .routes.automations import router as automations_router  # noqa: E402
from .routes.csrf_guard import (  # noqa: E402
    cleanup_csrf_tokens,
    client_key,
    require_action_guard,
)
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
from .services.auto_relink import AutoRelinkManager  # noqa: E402
from .services.media_automation import (  # noqa: E402
    build_media_automation_config,
    MediaAutomationConfig,
    MediaAutomationError,
    MediaAutomationManager,
)
from .services.notifications import NotificationCenter  # noqa: E402
from .services.organization_runs import OrganizationRunStore  # noqa: E402
from .services.organizer import VerifiedResumeManager  # noqa: E402
from .services.metadata import MediaMetadataResolver  # noqa: E402
from .services.ratio_monitor import RatioMonitor  # noqa: E402
from .services.stats import StatsStore  # noqa: E402
from .services.tracker_stats import TrackerStatsStore  # noqa: E402

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
    await app.state.auto_relink.start()
    yield
    await app.state.auto_relink.stop()
    await app.state.media_automation.stop()
    await app.state.qbit.close()


init_sentry(os.getenv("SENTRY_DSN", ""), os.getenv("SENTRY_ENVIRONMENT", "production"))

app = FastAPI(title="Torrent Panel", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

_COMMON_CSS_DIR = Path(sys.modules["common"].__file__).resolve().parent / "css"
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
app.state.auto_relink = AutoRelinkManager(
    app.state.qbit,
    app.state.notifications,
    enabled=AUTO_RELINK_ENABLED,
    interval_seconds=AUTO_RELINK_INTERVAL_SECONDS,
)
app.state.organize_lock = asyncio.Lock()
app.state.verified_resume = VerifiedResumeManager(
    app.state.qbit,
    ORGANIZER_RESUME_STATE_PATH,
    poll_seconds=ORGANIZER_RESUME_POLL_SECONDS,
)
app.state.organization_runs = OrganizationRunStore(LIBRARY_ORGANIZER_RUNS_PATH)
app.state.metadata_resolver = MediaMetadataResolver()
app.state.automation_rules = AutomationRuleStore(AUTOMATION_RULES_STATE_PATH)
app.state.tracker_stats = TrackerStatsStore(TRACKER_STATS_STATE_PATH)
app.state.stats = StatsStore(STATS_STATE_PATH, history_days=STATS_HISTORY_DAYS)
app.state.ratio_monitor = RatioMonitor(RATIO_MONITOR_STATE_PATH, threshold=RATIO_ALERT_THRESHOLD)
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
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    if (
        "/api/" in request.url.path
        or request.url.path == "/"
        or request.url.path.endswith("/")
        or request.url.path.endswith("/config.js")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


def error_detail(code: str, message: str, recovery: str) -> dict[str, str]:
    from common import error_detail as _ed
    return _ed(code, message, recovery)


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
                f'  torrentPanelPrefix: "{PUBLIC_PREFIX or ""}",',
                f'  prowlarrPanelPrefix: "{PROWLARR_PANEL_PUBLIC_PREFIX or ""}",',
                f'  cloudPanelPrefix: "{os.getenv("CLOUD_PANEL_PUBLIC_PREFIX", "/cloud-panel").rstrip("/")}",',
                f'  activityPrefix: "{ACTIVITY_PUBLIC_PREFIX or ""}",',
                f'  storagePrefix: "{STORAGE_PUBLIC_PREFIX or ""}",',
                f'  mediaPrefix: "{MEDIA_PUBLIC_PREFIX or ""}",',
                f'  healthPrefix: "{HEALTH_PUBLIC_PREFIX or ""}",',
                f'  statsPrefix: "{STATS_PUBLIC_PREFIX or ""}",',
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
                f'  cloudPanelPrefix: "{os.getenv("CLOUD_PANEL_PUBLIC_PREFIX", "/cloud-panel").rstrip("/")}",',
                f'  activityPrefix: "{ACTIVITY_PUBLIC_PREFIX or ""}",',
                f'  storagePrefix: "{STORAGE_PUBLIC_PREFIX or ""}",',
                f'  mediaPrefix: "{MEDIA_PUBLIC_PREFIX or ""}",',
                f'  healthPrefix: "{HEALTH_PUBLIC_PREFIX or ""}",',
                f'  statsPrefix: "{STATS_PUBLIC_PREFIX or ""}",',
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
register_console_page(STATS_PUBLIC_PREFIX, "stats", "stats.html")


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
