"""CSRF protection and rate-limiting guard."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from common import error_detail
from common.csrf import client_key as _common_client_key
from common.csrf import cleanup_csrf_tokens as _common_cleanup
from common.csrf import csrf_cookie_matches as _common_cookie_matches
from common.csrf import csrf_token_is_valid as _common_csrf_valid
from common.csrf import set_csrf_cookie as _common_set_csrf

from ..config import CSRF_COOKIE, CSRF_TOKEN_TTL_SECONDS, MAX_CSRF_TOKENS, TRUSTED_PROXY_IPS

__all__ = ["cleanup_csrf_tokens", "csrf_token_is_valid", "csrf_cookie_matches", "set_csrf_cookie", "require_action_guard", "client_key"]


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


async def require_action_guard(request: Request, x_torrent_panel_csrf: str | None = Header(default=None)) -> None:
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
