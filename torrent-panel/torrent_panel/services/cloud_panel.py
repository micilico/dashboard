"""HTTP client for the cloud-panel internal API (server-to-server)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import CLOUD_PANEL_API_URL, CLOUD_PANEL_INTERNAL_TOKEN, CLOUD_PANEL_PUBLIC_PREFIX

logger = logging.getLogger("torrent_panel.cloud_panel")

_TIMEOUT_SECONDS = 30.0


class CloudPanelError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def rename_batch(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Rename a batch of mount-relative items via the cloud-panel.

    Each item is ``{"path": mount_rel_parent, "old_name": ..., "new_name": ...}``.
    """
    if not CLOUD_PANEL_API_URL:
        raise CloudPanelError("CLOUD_PANEL_API_URL non configuré.")
    if not CLOUD_PANEL_INTERNAL_TOKEN:
        raise CloudPanelError("CLOUD_PANEL_INTERNAL_TOKEN non configuré.")
    if not items:
        return {"results": [], "failed": 0}

    endpoint = f"{CLOUD_PANEL_API_URL}{CLOUD_PANEL_PUBLIC_PREFIX}/api/files/internal-rename-batch"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                endpoint,
                data={"items": __import__("json").dumps(items)},
                headers={"X-Internal-Token": CLOUD_PANEL_INTERNAL_TOKEN},
            )
        except httpx.RequestError as exc:
            logger.warning("cloud-panel rename-batch request failed: %s", exc.__class__.__name__)
            raise CloudPanelError("cloud-panel est injoignable.") from exc

    if response.status_code != 200:
        logger.warning("cloud-panel rename-batch refused with status %s", response.status_code)
        raise CloudPanelError("cloud-panel a refusé le renommage.")

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("cloud-panel rename-batch returned invalid JSON")
        raise CloudPanelError("Réponse cloud-panel invalide.") from exc
    return payload
