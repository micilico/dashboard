"""Dashboard Common – shared utilities for torrent-panel and prowlarr-panel."""

from .rate_limiter import RateLimiter
from .csrf import cleanup_csrf_tokens, client_key, csrf_cookie_matches, csrf_token_is_valid, set_csrf_cookie
from .security import build_csp, error_detail
from .types import ErrorDetail

__all__ = [
    "RateLimiter",
    "cleanup_csrf_tokens",
    "client_key",
    "csrf_cookie_matches",
    "csrf_token_is_valid",
    "set_csrf_cookie",
    "build_csp",
    "error_detail",
    "ErrorDetail",
]
