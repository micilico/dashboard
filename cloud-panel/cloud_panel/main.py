from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

_sys_path_root = Path(__file__).resolve().parents[2]
if str(_sys_path_root) not in sys.path:
    sys.path.insert(0, str(_sys_path_root))

from common import build_csp, RateLimiter
from common.monitoring import init_sentry

from .config import (
    PUBLIC_PREFIX,
    CSRF_TOKEN_TTL_SECONDS,
    MAX_CSRF_TOKENS,
    MAX_RATE_KEYS,
    RATE_LIMIT_CALLS,
    RATE_LIMIT_SECONDS,
    STATIC_DIR,
)
from .routes.files import router as files_router
from .routes.favorites import router as favorites_router
from .routes.share import router as share_router

from logging import basicConfig, getLogger

basicConfig(level=os.getenv("CLOUD_PANEL_LOG_LEVEL", "INFO"))
logger = getLogger("cloud_panel")

init_sentry(os.getenv("SENTRY_DSN", ""), os.getenv("SENTRY_ENVIRONMENT", "production"))

app = FastAPI(title="Cloud Panel", docs_url=None, redoc_url=None, openapi_url=None)

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
app.state.last_csrf_cleanup = 0.0


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = build_csp()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    if "/api/" in request.url.path:
        response.headers["Cache-Control"] = "no-store"
    now = time.monotonic()
    if now - request.app.state.last_csrf_cleanup > 300:
        from .routes.csrf_guard import cleanup_csrf_tokens
        cleanup_csrf_tokens(request.app)
        request.app.state.last_csrf_cleanup = now
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config.js")
async def config_js() -> PlainTextResponse:
    return PlainTextResponse(
        "\n".join([
            "window.__CLOUD_PANEL_CONFIG__ = {",
            f'  publicPrefix: "{PUBLIC_PREFIX or ""}",',
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


app.include_router(files_router, prefix="/api")
app.include_router(favorites_router, prefix="/api")
app.include_router(share_router, prefix="/api")
if PUBLIC_PREFIX:
    app.include_router(files_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(favorites_router, prefix=f"{PUBLIC_PREFIX}/api")
    app.include_router(share_router, prefix=f"{PUBLIC_PREFIX}/api")
