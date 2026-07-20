"""Sentry monitoring integration (optional, self-hosted)."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("dashboard.monitoring")

_sensitive_keywords = re.compile(
    r"(api[_-]?key|apikey|passkey|token|password|secret|cookie|authorization|qbittorrent_password|jellyfin_api_key)",
    re.IGNORECASE,
)
_private_data_re = re.compile(
    r"magnet:\?\S+|https?://\S*(?:[^/@\s]+@|[?&](?:api[_-]?key|passkey|token|password|secret)=)\S*|/(?:Users|home)/[^/\s]+",
    re.IGNORECASE,
)


def _sanitize_event(event: dict, hint: dict) -> dict | None:
    if not isinstance(event, dict):
        return event
    _scrub_sensitive(event)
    return event


def _scrub_sensitive(data: Any, _depth: int = 0) -> None:
    if _depth > 10:
        return
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and _sensitive_keywords.search(key):
                data[key] = "[filtré]"
            elif isinstance(value, str) and _private_data_re.search(value):
                data[key] = "[données masquées]"
            else:
                _scrub_sensitive(value, _depth + 1)
    elif isinstance(data, list):
        for index, value in enumerate(data):
            if isinstance(value, str) and _private_data_re.search(value):
                data[index] = "[données masquées]"
            else:
                _scrub_sensitive(value, _depth + 1)


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
            before_send_transaction=_sanitize_event,
        )
        logger.info("Sentry initialized (environment=%s)", environment)
    except Exception as exc:
        logger.warning("Sentry initialization skipped: %s", exc)
