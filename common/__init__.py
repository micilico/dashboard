"""Dashboard Common – shared utilities for torrent-panel and prowlarr-panel."""

from pathlib import Path
import re
from typing import Optional

from .rate_limiter import RateLimiter
from .security import build_csp, error_detail
from .types import ErrorDetail

try:
    from .csrf import cleanup_csrf_tokens, client_key, csrf_cookie_matches, csrf_token_is_valid, set_csrf_cookie
except ModuleNotFoundError:  # pragma: no cover - allows frontend-only tooling without FastAPI installed
    cleanup_csrf_tokens = None
    client_key = None
    csrf_cookie_matches = None
    csrf_token_is_valid = None
    set_csrf_cookie = None

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
    "resolve_css_imports",
]

_IMPORT_RE = re.compile(r"""@import\s+url\((['"])([^'"]+)\1\)\s*;?""", re.IGNORECASE)


def resolve_css_imports(filepath: Path, seen: Optional[set] = None) -> str:
    if seen is None:
        seen = set()
    abs_file = filepath.resolve()
    if abs_file in seen:
        return ""
    seen.add(abs_file)
    text = abs_file.read_text(encoding="utf-8")

    def _replacer(m: re.Match) -> str:
        rel_path = m.group(2)
        imported = abs_file.parent / rel_path
        if not imported.exists():
            print(f"  [warn] @import not found: {imported}")
            return f"/* @import not found: {rel_path} */"
        return resolve_css_imports(imported, seen)

    return _IMPORT_RE.sub(_replacer, text)
