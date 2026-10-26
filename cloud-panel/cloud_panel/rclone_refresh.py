from __future__ import annotations

import logging
import queue
import threading
from urllib.parse import urlparse

import httpx

from .config import RCLONE_RC_PASS, RCLONE_RC_URL, RCLONE_RC_USER, RCLONE_REFRESH_MODE

logger = logging.getLogger(__name__)


def _is_loopback_rc_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "::1", "localhost"}


class TargetedRefreshQueue:
    """Best-effort, non-recursive rclone VFS refresh for changed directories.

    The queue is intentionally isolated from request and job threads. A slow or
    unavailable RC endpoint therefore cannot delay filesystem mutations. Only
    relative, sanitized directory paths are accepted; the public API never
    receives the RC URL or credentials.
    """

    def __init__(self, max_pending: int = 64) -> None:
        self._paths: queue.Queue[str | None] = queue.Queue(maxsize=max_pending)
        self._pending: set[str] = set()
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None

    def schedule(self, paths: list[str]) -> None:
        if RCLONE_REFRESH_MODE != "rc":
            return
        if not _is_loopback_rc_url(RCLONE_RC_URL):
            logger.warning("targeted rclone refresh disabled: RC URL must be loopback")
            return
        clean = {
            path.strip("/").replace("\\", "/")
            for path in paths
            if isinstance(path, str)
        }
        with self._guard:
            for path in clean:
                if path in self._pending:
                    continue
                try:
                    self._paths.put_nowait(path)
                    self._pending.add(path)
                except queue.Full:
                    logger.warning("rclone targeted refresh queue is full")
                    break
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name="cloud-rclone-refresh",
                    daemon=True,
                )
                self._thread.start()

    def _run(self) -> None:
        auth = (RCLONE_RC_USER, RCLONE_RC_PASS) if RCLONE_RC_USER else None
        with httpx.Client(auth=auth, timeout=httpx.Timeout(2.0, connect=0.5)) as client:
            while True:
                path = self._paths.get()
                try:
                    if path is None:
                        return
                    response = client.post(
                        f"{RCLONE_RC_URL}/vfs/refresh",
                        data={"dir": path, "recursive": "false"},
                    )
                    response.raise_for_status()
                except Exception as exc:
                    # RC availability and backend support vary by rclone and
                    # remote. Never fail the completed mutation for this hint.
                    logger.info("targeted rclone refresh unavailable (%s)", exc.__class__.__name__)
                finally:
                    if path is not None:
                        with self._guard:
                            self._pending.discard(path)
                        self._paths.task_done()


refresh_queue = TargetedRefreshQueue()


def schedule_targeted_refresh(paths: list[str]) -> None:
    refresh_queue.schedule(paths)
