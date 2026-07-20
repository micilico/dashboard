from __future__ import annotations

import secrets
import time

from fastapi import FastAPI, Request, Response


def cleanup_csrf_tokens(app_instance: FastAPI, csrf_token_ttl: int, max_csrf_tokens: int, now: float | None = None) -> None:
    current = now if now is not None else time.monotonic()
    tokens: dict[str, float] = app_instance.state.csrf_tokens
    for token, created_at in list(tokens.items()):
        if current - created_at > csrf_token_ttl:
            del tokens[token]
    if len(tokens) <= max_csrf_tokens:
        return
    for token, _created_at in sorted(tokens.items(), key=lambda item: item[1])[: len(tokens) - max_csrf_tokens]:
        del tokens[token]


def csrf_token_is_valid(app_instance: FastAPI, token: str, csrf_token_ttl: int, max_csrf_tokens: int) -> bool:
    cleanup_csrf_tokens(app_instance, csrf_token_ttl, max_csrf_tokens)
    return token in app_instance.state.csrf_tokens


def csrf_cookie_matches(request: Request, token: str, csrf_cookie_name: str) -> bool:
    for item in request.headers.get("cookie", "").split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name == csrf_cookie_name and secrets.compare_digest(value, token):
            return True
    return False


def set_csrf_cookie(
    app_instance: FastAPI,
    request: Request,
    response: Response,
    csrf_cookie_name: str,
    csrf_token_ttl: int,
    max_csrf_tokens: int,
    cookie_path: str = "/",
) -> str:
    is_https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    cleanup_csrf_tokens(app_instance, csrf_token_ttl, max_csrf_tokens)
    token = secrets.token_urlsafe(32)
    app_instance.state.csrf_tokens[token] = time.monotonic()
    response.set_cookie(
        csrf_cookie_name,
        token,
        secure=is_https,
        httponly=False,
        samesite="strict",
        path=cookie_path,
    )
    return token


def client_key(request: Request, trusted_proxy_ips: set[str]) -> str:
    client_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and client_host in trusted_proxy_ips:
        return forwarded.split(",", 1)[0].strip()
    return client_host
