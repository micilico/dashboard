from __future__ import annotations

from .types import ErrorDetail


def error_detail(code: str, message: str, recovery: str) -> ErrorDetail:
    return {"code": code, "message": message, "recovery": recovery}


def build_csp() -> str:
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "upgrade-insecure-requests",
        ]
    )
