from __future__ import annotations

import hashlib
import io
import logging
import os
import tempfile
import threading

from PIL import Image, UnidentifiedImageError

from ..config import THUMBNAIL_CACHE_DIR, THUMBNAIL_CACHE_MAX_FILES, THUMBNAIL_MAX_SOURCE_BYTES
from ..storage import download_file

logger = logging.getLogger(__name__)

THUMBNAIL_FORMAT_VERSION = "1"
THUMBNAIL_MAX_WIDTH = 1024
THUMBNAIL_QUALITY = 80

# Pillow cannot render these and they are already cheap to ship whole.
_RASTER_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "ico", "tif", "tiff", "avif",
}

_cache_guard = threading.Lock()
_inflight: dict[str, threading.Event] = {}
_created_since_prune = 0


def _cache_dir() -> str:
    directory = THUMBNAIL_CACHE_DIR or os.path.join(tempfile.gettempdir(), "cloud-panel-thumbs")
    return directory


def _prune(directory: str) -> None:
    """Keep the thumbnail cache bounded without walking it on every request."""
    global _created_since_prune
    if _created_since_prune < 32:
        return
    _created_since_prune = 0
    try:
        entries = [
            (entry.stat().st_mtime, entry.path)
            for entry in os.scandir(directory)
            if entry.is_file()
        ]
    except OSError:
        return
    excess = len(entries) - THUMBNAIL_CACHE_MAX_FILES
    if excess <= 0:
        return
    for _mtime, path in sorted(entries)[:excess]:
        try:
            os.remove(path)
        except OSError:
            pass


def _is_raster_image(name: str) -> bool:
    if "." not in name:
        return False
    return name.rsplit(".", 1)[-1].lower() in _RASTER_EXTENSIONS


def build_thumbnail(relative_path: str, width: int) -> tuple[bytes, str]:
    """Return ``(bytes, content_type)`` for a directory-listing thumbnail.

    The result is cached under ``/tmp`` so a grid re-render, a scroll or a
    second tab costs one file read instead of a fresh remote fetch plus a
    decode. Concurrent requests for the same image coalesce on one encode.
    """
    global _created_since_prune

    width = max(32, min(int(width), THUMBNAIL_MAX_WIDTH))
    filename = os.path.basename(relative_path)
    if not _is_raster_image(filename):
        raise ValueError('Aperçu indisponible pour ce fichier')

    file_path, stat_result = download_file(relative_path)
    if stat_result.st_size > THUMBNAIL_MAX_SOURCE_BYTES:
        raise ValueError('Fichier trop volumineux pour un aperçu')

    key = hashlib.sha256(
        "\0".join(
            (
                THUMBNAIL_FORMAT_VERSION,
                file_path,
                str(stat_result.st_mtime_ns),
                str(stat_result.st_size),
                str(width),
            )
        ).encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:40]
    directory = _cache_dir()
    cache_path = os.path.join(directory, f"{key}.webp")

    try:
        with open(cache_path, "rb") as cached:
            return cached.read(), "image/webp"
    except FileNotFoundError:
        pass
    except OSError:
        pass

    with _cache_guard:
        event = _inflight.get(key)
        if event is None:
            event = threading.Event()
            _inflight[key] = event
            leader = True
        else:
            leader = False

    if not leader:
        event.wait(timeout=30)
        try:
            with open(cache_path, "rb") as cached:
                return cached.read(), "image/webp"
        except OSError:
            raise ValueError('Aperçu indisponible pour ce fichier') from None

    try:
        data = _encode(file_path, width)
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as tmp_file:
                    tmp_file.write(data)
                os.replace(tmp_path, cache_path)
            except BaseException:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
            with _cache_guard:
                _created_since_prune += 1
            _prune(directory)
        except OSError:
            logger.debug("thumbnail cache write failed", exc_info=True)
        return data, "image/webp"
    finally:
        with _cache_guard:
            _inflight.pop(key, None)
        event.set()


def _encode(file_path: str, width: int) -> bytes:
    try:
        with Image.open(file_path) as source:
            source.draft("RGB", (width, width * 4))
            image = source.convert("RGBA" if _has_alpha(source) else "RGB")
            image.thumbnail((width, width * 4), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=THUMBNAIL_QUALITY, method=4)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError('Aperçu indisponible pour ce fichier') from exc
    except Exception as exc:  # Pillow may raise its own bomb/format errors.
        raise ValueError('Aperçu indisponible pour ce fichier') from exc


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA") or "transparency" in image.info
