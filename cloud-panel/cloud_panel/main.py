from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

_sys_path_root = Path(__file__).resolve().parents[2]
if str(_sys_path_root) not in sys.path:
    sys.path.insert(0, str(_sys_path_root))

from common import build_csp, RateLimiter
from common.monitoring import init_sentry
from common.csrf import cleanup_csrf_tokens as _common_cleanup
from common.csrf import csrf_cookie_matches, csrf_token_is_valid

from .config import (
    PUBLIC_PREFIX,
    CSRF_COOKIE,
    CSRF_TOKEN_TTL_SECONDS,
    MAX_CSRF_TOKENS,
    MAX_RATE_KEYS,
    RATE_LIMIT_CALLS,
    RATE_LIMIT_SECONDS,
    STATIC_DIR,
    TRUSTED_PROXY_IPS,
)
from .routes.files import router as files_router

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


def cleanup_csrf_tokens(app_instance, now=None):
    return _common_cleanup(app_instance, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS, now)


def client_key(request: Request) -> str:
    from common.csrf import client_key as _ck
    return _ck(request, TRUSTED_PROXY_IPS)


def require_action_guard(request: Request, x_cloud_panel_csrf: str | None = None):
    from fastapi import Header
    if x_cloud_panel_csrf is None:
        x_cloud_panel_csrf = request.headers.get("X-Cloud-Panel-CSRF")
    if (
        not x_cloud_panel_csrf
        or not csrf_cookie_matches(request, x_cloud_panel_csrf, CSRF_COOKIE)
        or not csrf_token_is_valid(request.app, x_cloud_panel_csrf, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS)
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "csrf_expired", "message": "Session de protection expiree.", "recovery": "Actualiser la session"},
        )
    if not request.app.state.action_limiter.allow(client_key(request)):
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": "Trop d'actions en peu de temps.", "recovery": "Reessayer"},
        )


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
if PUBLIC_PREFIX:
    app.include_router(files_router, prefix=f"{PUBLIC_PREFIX}/api")
