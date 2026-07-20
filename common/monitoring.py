"""Sentry monitoring integration (optional, self-hosted)."""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("dashboard.monitoring")

_sensitive_keywords = re.compile(
    r"(api[_-]?key|apikey|passkey|token|password|secret|cookie|authorization|qbittorrent_password|jellyfin_api_key)",
    re.IGNORECASE,
)
_private_data_re = re.compile(r"https?://[^\s]+@\S+|magnet:\?\S+", re.IGNORECASE)


def _sanitize_event(event: dict, hint: dict) -> dict | None:
    if not isinstance(event, dict):
        return event
    _scrub_sensitive(event)
    return event


def _scrub_sensitive(data: dict, _depth: int = 0) -> None:
    if _depth > 10:
        return
    keys_to_remove = []
    for key, value in data.items():
        if isinstance(key, str) and _sensitive_keywords.search(key):
            keys_to_remove.append(key)
        elif isinstance(value, str) and _private_data_re.search(value):
            data[key] = "[données masquées]"
        elif isinstance(value, dict):
            _scrub_sensitive(value, _depth + 1)
    for key in keys_to_remove:
        data[key] = "[filtré]"


def init_sentry(dsn: str, environment: str = "production") -> None:
    """Initialize Sentry SDK if a DSN is provided. No-op otherwise."""
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.httpx import HttpxIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            integrations=[
                FastApiIntegration(
                    transaction_style="endpoint",
                ),
                HttpxIntegration(),
            ],
            traces_sample_rate=0.1,
            send_default_pii=False,
            before_send=_sanitize_event,
        )
        logger.info("Sentry initialized (environment=%s)", environment)
    except Exception as exc:
        logger.warning("Sentry initialization skipped: %s", exc)
