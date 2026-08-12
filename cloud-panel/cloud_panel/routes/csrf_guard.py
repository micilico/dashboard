"""CSRF protection and rate-limiting guard."""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, Request

from common import error_detail, require_csrf_token
from common.csrf import client_key as _common_client_key
from common.csrf import cleanup_csrf_tokens as _common_cleanup
from common.csrf import csrf_cookie_matches as _common_cookie_matches
from common.csrf import csrf_token_is_valid as _common_csrf_valid
from common.csrf import set_csrf_cookie as _common_set_csrf
from ..config import INTERNAL_AUTOMATION_TOKEN

from ..config import CSRF_COOKIE, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS, TRUSTED_PROXY_IPS

__all__ = [
    "cleanup_csrf_tokens", "csrf_token_is_valid", "csrf_cookie_matches",
    "set_csrf_cookie", "require_action_guard", "client_key",
]


def cleanup_csrf_tokens(app_instance, now=None):
    return _common_cleanup(app_instance, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS, now)


def csrf_token_is_valid(app_instance, token):
    return _common_csrf_valid(app_instance, token, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS)


def csrf_cookie_matches(request, token):
    return _common_cookie_matches(request, token, CSRF_COOKIE)


def set_csrf_cookie(request, response):
    return _common_set_csrf(request.app, request, response, CSRF_COOKIE, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS, cookie_path="/")


def client_key(request):
    return _common_client_key(request, TRUSTED_PROXY_IPS)


async def require_action_guard(request: Request, x_cloud_panel_csrf: Optional[str] = Header(default=None)) -> None:
    internal_token = request.headers.get("X-Cloud-Panel-Internal-Token", "")
    if INTERNAL_AUTOMATION_TOKEN and internal_token == INTERNAL_AUTOMATION_TOKEN and client_key(request) in {"127.0.0.1", "::1"}:
        return
    require_csrf_token(
        request.app,
        request,
        x_cloud_panel_csrf,
        CSRF_COOKIE,
        CSRF_TOKEN_TTL_SECONDS,
        MAX_CSRF_TOKENS,
    )

    if not request.app.state.action_limiter.allow(client_key(request)):
        raise HTTPException(
            status_code=429,
            detail=error_detail("rate_limited", "Trop d'actions en peu de temps.", "Réessayer"),
        )
