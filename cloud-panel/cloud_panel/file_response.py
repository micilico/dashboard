from __future__ import annotations

from typing import Any

import anyio
from starlette.datastructures import MutableHeaders
from starlette.responses import FileResponse
from starlette.types import Send

from .config import DOWNLOAD_CHUNK_SIZE

# ``limiter`` is an ``anyio.CapacityLimiter``; None keeps Starlette's default.
DownloadLimiter = Any

_UNAVAILABLE_BODY = b"File not available"
_UNAVAILABLE_HEADERS = [
    (b"content-type", b"text/plain; charset=utf-8"),
    (b"content-length", str(len(_UNAVAILABLE_BODY)).encode("latin-1")),
]


class StreamingFileResponse(FileResponse):
    """``FileResponse`` tuned for remote (rclone/FUSE) mounts.

    Two changes matter for the time-to-first-byte and for the throughput:

    * reads are ``DOWNLOAD_CHUNK_SIZE`` (1 MiB) instead of 64 KiB, which avoids
      most of the thread hand-offs and gives the mount a chance to read ahead;
    * the blocking file reads run on a dedicated limiter, so concurrent
      downloads never starve listing, search or SSE work.

    The file is opened before the status line is sent, so a vanished file
    answers 404 instead of truncating a "successful" download.
    """

    chunk_size = DOWNLOAD_CHUNK_SIZE

    def __init__(self, *args: Any, limiter: DownloadLimiter | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.limiter = limiter

    async def _run_sync(self, func: Any, *args: Any) -> Any:
        return await anyio.to_thread.run_sync(func, *args, limiter=self.limiter)

    async def _handle_simple(self, send: Send, send_header_only: bool, send_pathsend: bool) -> None:
        await self._send(send, self.status_code, self.raw_headers, 0, None, send_header_only)

    async def _handle_single_range(
        self,
        send: Send,
        start: int,
        end: int,
        file_size: int,
        send_header_only: bool,
    ) -> None:
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-range"] = f"bytes {start}-{end - 1}/{file_size}"
        headers["content-length"] = str(end - start)
        await self._send(send, 206, headers.raw, start, end, send_header_only)

    async def _send(
        self,
        send: Send,
        status_code: int,
        raw_headers: list[tuple[bytes, bytes]],
        start: int,
        end: int | None,
        send_header_only: bool,
    ) -> None:
        try:
            handle = await self._run_sync(open, self.path, "rb")
        except OSError:
            await send({"type": "http.response.start", "status": 404, "headers": _UNAVAILABLE_HEADERS})
            await send({"type": "http.response.body", "body": _UNAVAILABLE_BODY, "more_body": False})
            return

        try:
            await send({"type": "http.response.start", "status": status_code, "headers": raw_headers})
            if send_header_only:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
            if start:
                await self._run_sync(handle.seek, start)
            remaining = None if end is None else end - start
            while True:
                size = self.chunk_size if remaining is None else min(self.chunk_size, remaining)
                if size <= 0:
                    break
                chunk = await self._run_sync(handle.read, size)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await self._run_sync(handle.close)
