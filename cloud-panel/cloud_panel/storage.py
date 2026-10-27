from __future__ import annotations

import asyncio
import logging
import hashlib
import mimetypes
import os
import posixpath
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from stat import S_ISREG
from typing import Callable

import httpx
from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from .config import FOLDER_SIZE_CACHE_MAX_ENTRIES, MOUNT_PATH, PATH_LOCK_MAX_ENTRIES, RESOLVE_CACHE_MAX_ENTRIES, RESOLVE_CACHE_TTL, SCANDIR_CACHE_MAX_ENTRIES, SCANDIR_CACHE_TTL, SEARCH_CACHE_MAX_ENTRIES, SEARCH_CACHE_TTL, SEARCH_MAX_RESULTS, SEARCH_MIN_LENGTH, TEXT_EDITOR_MAX_BYTES, TRASH_DIR_NAME, UPLOAD_CHUNK_SIZE
from .rclone_refresh import schedule_targeted_refresh
from .security import resolve_path_within

logger = logging.getLogger(__name__)

_scandir_cache: dict[tuple[str, float], tuple[float, list[dict]]] = {}
_folder_size_cache: dict[tuple[str, float], tuple[float, int]] = {}
_resolve_cache: dict[str, tuple[float, str]] = {}
_path_locks: dict[str, threading.Lock] = {}
_search_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}
_path_locks_guard = threading.Lock()
_cache_guard = threading.RLock()
_cache_generation = 0
_mount_real_guard = threading.Lock()
_mount_real_source = MOUNT_PATH
_mount_real_value = os.path.realpath(MOUNT_PATH)


@dataclass
class _InflightResult:
    event: threading.Event = field(default_factory=threading.Event)
    result: object | None = None
    error: BaseException | None = None
    completed_at: float = 0.0


_scandir_inflight: dict[tuple[str, float, int], _InflightResult] = {}
_search_inflight: dict[tuple, _InflightResult] = {}


def _resolve(relative_path: str, *, must_exist: bool = True) -> str:
    """Resolve a client path, memoizing the containment check per path.

    Every resolution walks the path one component at a time (``realpath``) and
    stats the result. On a remote/FUSE mount those calls add up, and the same
    file is requested repeatedly (listing, preview, thumbnail, download). The
    memo keeps the security check identical while making repeat lookups free;
    it is dropped on every mutation and expires after ``RESOLVE_CACHE_TTL``.
    """
    key = relative_path if isinstance(relative_path, str) else ""
    now = time.monotonic()
    with _cache_guard:
        cached = _resolve_cache.get(key)
    if cached is not None and now - cached[0] < RESOLVE_CACHE_TTL:
        return cached[1]

    resolved = resolve_path_within(
        MOUNT_PATH,
        relative_path,
        must_exist=must_exist,
        base_real=_mount_real(),
    )
    if must_exist:
        with _cache_guard:
            if len(_resolve_cache) >= RESOLVE_CACHE_MAX_ENTRIES:
                for stale_key, _value in sorted(
                    _resolve_cache.items(), key=lambda item: item[1][0]
                )[: max(1, len(_resolve_cache) // 4)]:
                    _resolve_cache.pop(stale_key, None)
            _resolve_cache[key] = (now, resolved)
    return resolved


def forget_resolution(relative_path: str) -> None:
    """Drop one memoized resolution so the next call revalidates on the mount."""
    key = relative_path if isinstance(relative_path, str) else ""
    with _cache_guard:
        _resolve_cache.pop(key, None)


def _canonical_from_target(target: str) -> str:
    mount_real = _mount_real()
    if os.path.realpath(target) == mount_real:
        return ''
    return _rel(os.path.realpath(target), mount_real)


def _cache_epoch() -> int:
    with _cache_guard:
        return _cache_generation


def get_disk_usage() -> dict[str, str | float | bool]:
    """Disk usage placeholder; remote quota monitoring is disabled."""
    return {"disk_used": "N/A", "disk_total": "N/A", "disk_percent": 0, "available": False}


def get_cached_scandir(path: str, ttl: float = SCANDIR_CACHE_TTL) -> list[dict]:
    """Return a directory snapshot while preserving the public list contract."""
    items, _scanned_at = _get_cached_scandir_with_timestamp(path, ttl)
    return items


def _get_cached_scandir_with_timestamp(
    path: str,
    ttl: float = SCANDIR_CACHE_TTL,
) -> tuple[list[dict], float]:
    """Return one directory snapshot, coalescing concurrent scans.

    A failed scan is never cached. If a mutation invalidates the cache while a
    scan is running, its result is returned to the waiting callers but is not
    written into the new cache generation.
    """
    now = time.monotonic()
    epoch = _cache_epoch()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    cache_key = (path, mtime)
    with _cache_guard:
        cached = _scandir_cache.get(cache_key)
        if cached is not None:
            cached_time, cached_result = cached
        else:
            cached_time = 0.0
            cached_result = None
        if now - cached_time < ttl:
            return list(cached_result or []), cached_time
        inflight_key = (*cache_key, epoch)
        inflight = _scandir_inflight.get(inflight_key)
        if inflight is None:
            inflight = _InflightResult()
            _scandir_inflight[inflight_key] = inflight
            leader = True
        else:
            leader = False

    if not leader:
        inflight.event.wait()
        if inflight.error is not None:
            raise inflight.error
        return list(inflight.result or []), inflight.completed_at

    result: list[dict] = []
    try:
        for entry in os.scandir(path):
            if entry.name == TRASH_DIR_NAME:
                continue
            stat = entry.stat()
            is_dir = entry.is_dir()
            size_bytes = 0 if is_dir else stat.st_size
            result.append({
                'name': entry.name,
                'is_dir': is_dir,
                'path': _rel(entry.path, _mount_real()),
                'size': '' if is_dir else format_size(size_bytes),
                'size_bytes': size_bytes,
                'modified': int(stat.st_mtime),
                'created': int(getattr(stat, 'st_ctime', 0)),
            })
        result.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        inflight.result = result
        completed_at = time.monotonic()
        inflight.completed_at = completed_at
        with _cache_guard:
            if epoch == _cache_generation:
                _scandir_cache[cache_key] = (completed_at, result)
                _purge_cache(_scandir_cache, SCANDIR_CACHE_MAX_ENTRIES)
        return list(result), completed_at
    except Exception as exc:
        inflight.error = exc
        logger.warning('scandir failed (%s)', exc.__class__.__name__)
        raise
    finally:
        with _cache_guard:
            _scandir_inflight.pop(inflight_key, None)
        inflight.event.set()


def _purge_cache(cache: dict, max_entries: int) -> None:
    if len(cache) <= max_entries:
        return
    for key, _value in sorted(cache.items(), key=lambda item: item[1][0])[: len(cache) - max_entries]:
        cache.pop(key, None)


def clear_scandir_cache() -> None:
    global _cache_generation
    with _cache_guard:
        _cache_generation += 1
        _scandir_cache.clear()
        _search_cache.clear()
        _resolve_cache.clear()


def clear_folder_size_cache() -> None:
    global _cache_generation
    with _cache_guard:
        _cache_generation += 1
        _folder_size_cache.clear()


def invalidate_after_mutation(*relative_paths: str) -> None:
    """Invalidate local caches and queue non-recursive remote refresh hints."""
    clear_scandir_cache()
    clear_folder_size_cache()
    schedule_targeted_refresh([path for path in relative_paths if isinstance(path, str)])


def _target_lock(path: str) -> threading.Lock:
    """Return a per-path lock so concurrent mutations of the same target serialize."""
    real = os.path.realpath(path)
    with _path_locks_guard:
        if len(_path_locks) >= PATH_LOCK_MAX_ENTRIES:
            for key, candidate in list(_path_locks.items()):
                if not candidate.locked():
                    _path_locks.pop(key, None)
                    if len(_path_locks) < PATH_LOCK_MAX_ENTRIES:
                        break
        lock = _path_locks.get(real)
        if lock is None:
            lock = threading.Lock()
            _path_locks[real] = lock
        return lock


def _unique_name(dest_dir: str, name: str) -> str:
    """Resolve a target path in dest_dir, appending ' (copie)' / ' (copie N)' on conflict."""
    candidate = os.path.join(dest_dir, name)
    if not os.path.exists(candidate):
        return candidate
    base, ext = os.path.splitext(name)
    for i in (2, 3, 4, 5, 6, 7, 8, 9, 10):
        label = "copie" if i == 2 else f"copie {i - 1}"
        candidate = os.path.join(dest_dir, f"{base} ({label}){ext}")
        if not os.path.exists(candidate):
            return candidate
    raise ValueError('Conflit de nom trop frequent')


def _record_history(filename: str, size_bytes: int, path: str, action: str = "upload", token: str | None = None) -> None:
    try:
        from .models import add_history_entry
        add_history_entry(filename, size_bytes, path, token=token, action=action)
    except Exception:
        logger.debug('Could not record history entry', exc_info=True)


def _is_trash_rel(rel: str) -> bool:
    rel = (rel or "").replace(os.sep, "/")
    return rel == TRASH_DIR_NAME or rel.startswith(TRASH_DIR_NAME + "/")


def _trash_root() -> str:
    return os.path.join(MOUNT_PATH, TRASH_DIR_NAME)


def _folder_size_cached(
    abs_path: str,
    ttl: float = SCANDIR_CACHE_TTL,
    progress: Callable[[int], None] | None = None,
) -> int:
    """Recursively sum file sizes under abs_path, cached by (path, mtime)."""
    generation = _cache_epoch()
    try:
        mtime = os.path.getmtime(abs_path)
    except OSError:
        mtime = 0
    cache_key = (abs_path, mtime)
    with _cache_guard:
        cached = _folder_size_cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < ttl:
        return cached[1]
    total = 0
    seen = 0
    try:
        for root, dirs, files in os.walk(abs_path, followlinks=False):
            dirs.sort(key=str.casefold)
            for fname in files:
                try:
                    total += os.path.getsize(os.path.join(root, fname))
                except OSError:
                    raise
                seen += 1
                if progress is not None and seen % 100 == 0:
                    progress(seen)
    except OSError as exc:
        logger.warning('folder size walk failed (%s)', exc.__class__.__name__)
        raise
    if progress is not None:
        progress(seen)
    # A mutation may invalidate the cache while this recursive walk is still
    # running. Never let that older walk repopulate the new cache generation.
    with _cache_guard:
        if generation == _cache_generation:
            _folder_size_cache[cache_key] = (time.monotonic(), total)
            _purge_cache(_folder_size_cache, FOLDER_SIZE_CACHE_MAX_ENTRIES)
    return total


def get_folder_size(
    relative_path: str,
    name: str,
    progress: Callable[[int], None] | None = None,
) -> dict:
    """Return the recursive size of a folder as display string + bytes."""
    parent_dir = _resolve(relative_path)
    if not os.path.isdir(parent_dir):
        raise ValueError('Dossier introuvable')
    target = os.path.join(parent_dir, name)
    if not os.path.isdir(target):
        raise ValueError('Dossier introuvable')
    size_bytes = _folder_size_cached(os.path.realpath(target), progress=progress)
    return {
        'name': name,
        'size': format_size(size_bytes),
        'size_bytes': size_bytes,
        'path': _rel(target, _mount_real()),
    }


def get_folder_sizes(
    paths: list[str],
    progress: Callable[[int, int], None] | None = None,
    entries_progress: Callable[[int, int, int], None] | None = None,
) -> dict:
    """Compute the recursive size of several folders in one batch.

    Invalid or non-directory paths are counted in ``failed`` instead of
    raising, so a single request can compute all visible folders safely.
    ``entries_progress`` receives ``(folder_index, folder_total, entries_seen)``
    so a long walk stays observable while a single folder is scanned.
    """
    items: list[dict] = []
    failed = 0
    total = len(paths)
    for index, raw_rel in enumerate(paths, start=1):
        rel = raw_rel.strip() if isinstance(raw_rel, str) else raw_rel
        if not rel:
            continue
        entries_callback = None
        if entries_progress is not None:
            entries_callback = lambda seen, idx=index: entries_progress(idx, total, seen)
        try:
            target = _resolve(rel)
            if not os.path.isdir(target):
                failed += 1
                continue
            size_bytes = _folder_size_cached(
                os.path.realpath(target),
                progress=entries_callback,
            )
            items.append({
                'path': _rel(target, _mount_real()),
                'name': rel.replace("/", os.sep).rsplit(os.sep, 1)[-1],
                'size': format_size(size_bytes),
                'size_bytes': size_bytes,
            })
        except (ValueError, OSError):
            failed += 1
        finally:
            if progress is not None:
                progress(index, total)
    return {'items': items, 'total': total, 'failed': failed}


def format_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def list_directory(relative_path: str = '', offset: int = 0, limit: int = 50, search: str = '') -> dict:
    """List directory contents with metadata."""
    target_dir = _resolve(relative_path)
    if not os.path.isdir(target_dir):
        raise ValueError('Dossier introuvable')
    all_items, scanned_at = _get_cached_scandir_with_timestamp(target_dir)
    if search:
        query = search.casefold()
        all_items = [item for item in all_items if query in item['name'].casefold()]
    total = len(all_items)
    items = all_items[max(0, offset):max(0, offset) + max(1, min(limit, 200))]
    usage = get_disk_usage()
    disk_used = usage["disk_used"]
    disk_total = usage["disk_total"]
    disk_percent = usage["disk_percent"]
    source_age_ms = max(0, int((time.monotonic() - scanned_at) * 1000))
    source_scanned_at = (
        datetime.now(UTC) - timedelta(milliseconds=source_age_ms)
    ).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    return {
        'items': items,
        'total': total,
        'has_more': max(0, offset) + len(items) < total,
        'current_path': relative_path,
        'disk_used': disk_used,
        'disk_total': disk_total,
        'disk_percent': disk_percent,
        'disk_available': usage.get("available", True),
        'path': _canonical_from_target(target_dir),
        'source_age_ms': source_age_ms,
        'source_max_age_ms': max(0, int(SCANDIR_CACHE_TTL * 1000)),
        'source_scanned_at': source_scanned_at,
    }


def canonical_directory(relative_path: str) -> str:
    """Return the canonical mount-relative path for an existing directory."""
    target = _resolve(relative_path)
    if not os.path.isdir(target):
        raise ValueError('Dossier introuvable')
    return _canonical_from_target(target)


def directory_summary(relative_path: str) -> dict:
    """Return a live fingerprint covering every entry in a directory."""
    target = _resolve(relative_path)
    if not os.path.isdir(target):
        raise ValueError('Dossier introuvable')
    items, scanned_at = _get_cached_scandir_with_timestamp(target)
    fingerprint = hashlib.sha256()
    for item in items:
        fingerprint.update(
            f"{item.get('name', '')}\0{item.get('is_dir', False)}\0"
            f"{item.get('size_bytes', 0)}\0{item.get('modified', 0)}\0"
            f"{item.get('created', 0)}\n".encode('utf-8', errors='surrogatepass')
        )
    canonical_path = _canonical_from_target(target)
    source_age_ms = max(0, int((time.monotonic() - scanned_at) * 1000))
    source_scanned_at = (
        datetime.now(UTC) - timedelta(milliseconds=source_age_ms)
    ).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    return {
        'path': canonical_path,
        'current_path': canonical_path,
        'fingerprint': fingerprint.hexdigest(),
        'total': len(items),
        'source_age_ms': source_age_ms,
        'source_max_age_ms': max(0, int(SCANDIR_CACHE_TTL * 1000)),
        'source_scanned_at': source_scanned_at,
        'observed_at': time.time(),
    }


_INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(filename: str) -> str:
    """Remove path separators, dangerous characters, trailing dots/spaces and reject reserved names."""
    name = _INVALID_FILENAME_RE.sub('_', filename)
    name = name.rstrip(" .")
    if not name or name in ('.', '..'):
        raise ValueError('Nom de fichier invalide')
    if name.split('.', 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError('Nom de fichier invalide')
    return name


async def upload_file_streaming(
    relative_path: str,
    file: UploadFile,
    overwrite: str = "rename",
) -> dict:
    """Upload file with streaming chunk-by-chunk, write to .tmp then atomic rename.

    ``overwrite`` controls conflicts: "rename" (default, auto-suffix), "overwrite",
    or "skip" (keep the existing file and report it).
    """
    if not file.filename:
        raise ValueError('Nom de fichier requis')

    filename = sanitize_filename(file.filename)
    def prepare_target() -> tuple[str, dict | None]:
        target_dir = _resolve(relative_path)
        if not os.path.isdir(target_dir):
            raise ValueError('Dossier destination introuvable')
        final_path = os.path.join(target_dir, filename)
        if overwrite == "rename":
            final_path = _unique_name(target_dir, filename)
        elif overwrite == "skip" and os.path.exists(final_path):
            return final_path, {
                'success': False,
                'skipped': True,
                'filename': filename,
                'path': _rel(final_path, _mount_real()),
            }
        return final_path, None

    final_path, skipped = await _await_thread_complete(prepare_target)
    if skipped is not None:
        return skipped

    tmp_path = final_path + '.tmp'
    try:
        total_size = 0
        upload_stream = await _await_thread_complete(open, tmp_path, 'wb')
        try:
            while chunk := await _await_complete_on_cancel(file.read(UPLOAD_CHUNK_SIZE)):
                await _await_thread_complete(upload_stream.write, chunk)
                total_size += len(chunk)
        finally:
            await _await_thread_complete(upload_stream.close)

        await _await_thread_complete(_replace_uploaded_file, final_path, tmp_path)
        invalidate_after_mutation(relative_path)

        await _await_thread_complete(
            _record_history,
            os.path.basename(final_path),
            total_size,
            _rel(final_path, _mount_real()),
            "upload",
            None,
        )

        return {
            'success': True,
            'filename': os.path.basename(final_path),
            'size': format_size(total_size),
            'path': _rel(final_path, _mount_real()),
        }
    except asyncio.CancelledError:
        await _await_thread_complete(_remove_if_exists, tmp_path)
        logger.info('Upload cancelled before commit')
        raise
    except Exception:
        await _await_thread_complete(_remove_if_exists, tmp_path)
        logger.exception('Upload failed')
        raise


async def _await_complete_on_cancel(awaitable):
    """Wait for an in-flight upload operation before cleanup."""
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except BaseException:
            pass
        raise


async def _await_thread_complete(func, *args):
    return await _await_complete_on_cancel(run_in_threadpool(func, *args))


def _replace_uploaded_file(final_path: str, tmp_path: str) -> None:
    with _target_lock(final_path):
        os.replace(tmp_path, final_path)


def _remove_if_exists(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def download_file(relative_path: str) -> tuple[str, os.stat_result]:
    """Return the absolute path and metadata for a file download.

    The stat is returned so the HTTP layer can build ``Content-Length``,
    ``Last-Modified`` and ``ETag`` without stat-ing the mount a second time.
    """
    file_path = _resolve(relative_path)
    try:
        stat_result = os.stat(file_path)
    except OSError as exc:
        forget_resolution(relative_path)
        raise ValueError('Fichier introuvable') from exc
    if not S_ISREG(stat_result.st_mode):
        raise ValueError('Fichier introuvable')
    return file_path, stat_result


def create_directory(relative_path: str, dirname: str) -> dict:
    """Create a new directory."""
    parent_dir = _resolve(relative_path)
    if not os.path.isdir(parent_dir):
        raise ValueError('Dossier parent introuvable')

    dirname = sanitize_filename(dirname)
    new_dir = os.path.join(parent_dir, dirname)
    if os.path.exists(new_dir):
        raise ValueError('Ce dossier existe deja')

    os.makedirs(new_dir, exist_ok=True)
    invalidate_after_mutation(relative_path)

    return {
        'success': True,
        'name': dirname,
        'path': _rel(new_dir, _mount_real()),
    }


def rename_item(relative_path: str, old_name: str, new_name: str) -> dict:
    """Rename a file or directory."""
    parent_dir = _resolve(relative_path)
    old_path = os.path.join(parent_dir, old_name)
    new_name = sanitize_filename(new_name)
    new_path = os.path.join(parent_dir, new_name)

    if not os.path.exists(old_path):
        raise ValueError('Element introuvable')
    if os.path.exists(new_path):
        raise ValueError('Ce nom existe deja')

    with _target_lock(old_path):
        os.rename(old_path, new_path)
    invalidate_after_mutation(relative_path)

    try:
        size_bytes = os.path.getsize(new_path) if os.path.isfile(new_path) else 0
        _record_history(new_name, size_bytes, _rel(new_path, _mount_real()), action="rename")
    except Exception:
        pass

    return {
        'success': True,
        'old_name': old_name,
        'new_name': new_name,
        'path': _rel(new_path, _mount_real()),
    }


def _rewrite_paths_after_move(old_rel: str, new_rel: str) -> None:
    """Keep favorites and share links pointing at the moved tree."""
    if old_rel == new_rel:
        return
    old_match = old_rel if old_rel.endswith('/') else old_rel + '/'
    new_match = new_rel if new_rel.endswith('/') else new_rel + '/'
    try:
        from .models import get_db
        with get_db() as db:
            for table in ("favorites", "share_links"):
                db.execute(
                    f"""UPDATE {table}
                        SET path = CASE
                            WHEN path = ? THEN ?
                            ELSE ? || substr(path, ?)
                        END
                        WHERE path = ? OR (length(path) > ? AND substr(path, 1, ?) = ?)""",
                    (old_rel, new_rel, new_match, len(old_match) + 1, old_rel, len(old_match), len(old_match), old_match),
                )
    except Exception:
        logger.warning('Could not rewrite stored paths after move', exc_info=True)


def _rel(path: str, base: str) -> str:
    """Relative path normalized to forward slashes (used in URLs and storage)."""
    return os.path.relpath(path, base).replace(os.sep, "/")


def _mount_real() -> str:
    """Memoize realpath per configured mount while noticing remount changes."""
    global _mount_real_source, _mount_real_value
    if _mount_real_source == MOUNT_PATH:
        return _mount_real_value
    with _mount_real_guard:
        if _mount_real_source != MOUNT_PATH:
            _mount_real_source = MOUNT_PATH
            _mount_real_value = os.path.realpath(MOUNT_PATH)
        return _mount_real_value


def move_item(source_path: str, name: str, dest_path: str) -> dict:
    """Move a file or directory to another folder within the mount."""
    source_dir = _resolve(source_path)
    if not os.path.isdir(source_dir):
        raise ValueError('Dossier source introuvable')
    dest_dir = _resolve(dest_path)
    if not os.path.isdir(dest_dir):
        raise ValueError('Dossier destination introuvable')

    src = _resolve(posixpath.join(source_path, name))
    dest = _resolve(posixpath.join(dest_path, name), must_exist=False)

    src_real = os.path.realpath(src)
    src_parent_real = os.path.realpath(source_dir)
    dest_real = os.path.realpath(dest_dir)

    if dest_real == src_parent_real:
        raise ValueError('Deja au meme emplacement')
    if os.path.isdir(src) and (dest_real == src_real or dest_real.startswith(src_real + os.sep)):
        raise ValueError('Deplacement impossible dans lui-meme')
    if os.path.exists(dest):
        raise ValueError('Un element portant ce nom existe deja ici')

    mount_real = _mount_real()
    old_rel = _rel(src_real, mount_real)
    with _target_lock(src):
        shutil.move(src, dest)
    invalidate_after_mutation(source_path, dest_path)
    new_rel = _rel(os.path.realpath(dest), mount_real)
    _rewrite_paths_after_move(old_rel, new_rel)

    try:
        size_bytes = os.path.getsize(dest) if os.path.isfile(dest) else 0
        _record_history(name, size_bytes, new_rel, action="move")
    except Exception:
        pass

    return {
        'success': True,
        'name': name,
        'from_path': old_rel,
        'to_path': new_rel,
    }


def move_items(source_items: list[dict[str, str]], dest_path: str) -> dict:
    """Move several items after validating the complete operation."""
    dest_dir = _resolve(dest_path)
    if not os.path.isdir(dest_dir):
        raise ValueError('Dossier destination introuvable')
    prepared: list[tuple[str, str]] = []
    seen_destinations: set[str] = set()
    for item in source_items:
        source_path = str(item.get('path', ''))
        name = str(item.get('name', ''))
        if not name:
            raise ValueError('Nom d élément invalide')
        source_dir = _resolve(source_path)
        if not os.path.isdir(source_dir):
            raise ValueError('Dossier source introuvable')
        src = _resolve(posixpath.join(source_path, name))
        dest = _resolve(posixpath.join(dest_path, name), must_exist=False)
        src_real = os.path.realpath(src)
        if os.path.realpath(dest_dir) == os.path.realpath(source_dir):
            raise ValueError('Deja au meme emplacement')
        if os.path.isdir(src) and (os.path.realpath(dest_dir) == src_real or os.path.realpath(dest_dir).startswith(src_real + os.sep)):
            raise ValueError('Deplacement impossible dans lui-meme')
        dest_key = os.path.realpath(dest)
        if os.path.exists(dest) or dest_key in seen_destinations:
            raise ValueError('Un element portant ce nom existe deja ici')
        seen_destinations.add(dest_key)
        prepared.append((source_path, name))
    results = [move_item(source_path, name, dest_path) for source_path, name in prepared]
    return {'success': True, 'moved': len(results), 'items': results}


def _trash_unique_target(trashed_rel: str) -> str:
    """Resolve a unique absolute path inside the trash mirroring ``trashed_rel``."""
    trash_root = _trash_root()
    target = os.path.realpath(os.path.join(trash_root, trashed_rel))
    base_real = os.path.realpath(trash_root)
    if os.path.commonpath((base_real, target)) != base_real:
        raise ValueError('Chemin de corbeille invalide')
    if not os.path.exists(target):
        return target
    base, ext = os.path.splitext(trashed_rel)
    for i in (2, 3, 4, 5, 6, 7, 8, 9, 10):
        candidate = os.path.realpath(os.path.join(trash_root, f"{base} ({'copie' if i == 2 else f'copie {i - 1}'}){ext}"))
        if not os.path.exists(candidate):
            return candidate
    raise ValueError('Conflit de nom dans la corbeille')


def delete_item(relative_path: str, name: str, permanent: bool = False) -> dict:
    """Delete a file or directory. By default it is moved to the trash and
    tracked so it can be restored; ``permanent=True`` removes it for good."""
    parent_dir = _resolve(relative_path)
    target = os.path.join(parent_dir, name)

    if not os.path.exists(target):
        raise ValueError('Element introuvable')

    is_dir = os.path.isdir(target)
    mount_real = _mount_real()
    original_rel = _rel(os.path.realpath(target), mount_real)
    size_bytes = os.path.getsize(target) if os.path.isfile(target) else 0

    with _target_lock(target):
        if permanent:
            if is_dir:
                shutil.rmtree(target)
            else:
                os.remove(target)
        else:
            trash_root = _trash_root()
            os.makedirs(trash_root, exist_ok=True)
            trash_abs = _trash_unique_target(original_rel)
            trashed_rel = os.path.relpath(trash_abs, trash_root).replace(os.sep, "/")
            os.makedirs(os.path.dirname(trash_abs), exist_ok=True)
            shutil.move(target, trash_abs)
            try:
                from .models import add_trash_entry
                add_trash_entry(
                    original_path=original_rel,
                    name=name,
                    is_dir=is_dir,
                    trashed_rel=trashed_rel,
                    size_bytes=size_bytes,
                )
            except Exception:
                logger.warning('Could not record trash entry', exc_info=True)

    invalidate_after_mutation(relative_path)

    _record_history(name, size_bytes, original_rel, action="delete")

    return {
        'success': True,
        'name': name,
        'path': original_rel,
        'trashed': not permanent,
    }


def list_trash() -> list[dict]:
    from .models import get_trash_entries
    return get_trash_entries()


def restore_item(trashed_rel: str) -> dict:
    """Restore a trashed item to its original location (or a suffixed one on conflict)."""
    from .models import get_trash_entry, remove_trash_entry
    entry = get_trash_entry(trashed_rel)
    if not entry:
        raise ValueError('Element de corbeille introuvable')

    trash_root = _trash_root()
    trash_target = os.path.realpath(os.path.join(trash_root, entry["trashed_rel"]))
    if not os.path.exists(trash_target):
        raise ValueError('Fichier absent de la corbeille')

    original_abs = _resolve(entry["original_path"], must_exist=False)
    if os.path.exists(original_abs):
        original_abs = _unique_name(os.path.dirname(original_abs), os.path.basename(original_abs))

    os.makedirs(os.path.dirname(original_abs), exist_ok=True)
    with _target_lock(original_abs):
        shutil.move(trash_target, original_abs)

    remove_trash_entry(entry["id"])
    invalidate_after_mutation(os.path.dirname(entry["original_path"]).replace(os.sep, "/"))

    new_rel = _rel(os.path.realpath(original_abs), _mount_real())
    _record_history(entry["name"], entry.get("size_bytes", 0), new_rel, action="restore")
    return {'success': True, 'name': entry["name"], 'path': new_rel}


def empty_trash() -> dict:
    from .models import get_trash_entries, clear_trash_entries
    entries = get_trash_entries()
    trash_root = _trash_root()
    removed = 0
    for entry in entries:
        target = os.path.realpath(os.path.join(trash_root, entry["trashed_rel"]))
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            elif os.path.exists(target):
                os.remove(target)
            removed += 1
        except OSError:
            logger.warning('Could not purge trash item %s', entry["trashed_rel"], exc_info=True)
    clear_trash_entries()
    invalidate_after_mutation("")
    try:
        os.rmdir(trash_root)
    except OSError:
        pass
    return {'success': True, 'removed': removed}


def copy_item(relative_path: str, name: str, dest_path: str) -> dict:
    """Copy a file or directory to another folder within the mount."""
    source_dir = _resolve(relative_path)
    if not os.path.isdir(source_dir):
        raise ValueError('Dossier source introuvable')
    dest_dir = _resolve(dest_path)
    if not os.path.isdir(dest_dir):
        raise ValueError('Dossier destination introuvable')

    src = _resolve(posixpath.join(relative_path, name))
    dest_dir_real = os.path.realpath(dest_dir)
    src_real = os.path.realpath(src)
    if os.path.isdir(src) and (dest_dir_real == src_real or dest_dir_real.startswith(src_real + os.sep)):
        raise ValueError('Copie impossible dans le dossier lui-meme')
    target_path = _unique_name(dest_dir, name)

    with _target_lock(src):
        if os.path.isdir(src):
            shutil.copytree(src, target_path, symlinks=False)
        else:
            shutil.copy2(src, target_path)

    invalidate_after_mutation(relative_path, dest_path)

    copied_name = os.path.basename(target_path)
    size_bytes = os.path.getsize(target_path) if os.path.isfile(target_path) else 0
    _record_history(copied_name, size_bytes, _rel(os.path.realpath(target_path), _mount_real()), action="copy")

    return {
        'success': True,
        'name': copied_name,
        'path': _rel(os.path.realpath(target_path), _mount_real()),
    }


def create_text_file(relative_path: str, filename: str) -> dict:
    """Create an empty text file."""
    parent_dir = _resolve(relative_path)
    if not os.path.isdir(parent_dir):
        raise ValueError('Dossier parent introuvable')
    filename = sanitize_filename(filename)
    target = os.path.join(parent_dir, filename)
    if os.path.exists(target):
        raise ValueError('Ce fichier existe deja')
    with _target_lock(target):
        open(target, 'w', encoding='utf-8', newline='').close()
    invalidate_after_mutation(relative_path)
    _record_history(filename, 0, _rel(target, _mount_real()), action="create")
    return {'success': True, 'name': filename, 'path': _rel(target, _mount_real())}


def read_text_file(relative_path: str, max_bytes: int | None = None) -> dict:
    """Return the text content of a small file."""
    max_bytes = max_bytes or TEXT_EDITOR_MAX_BYTES
    file_path = _resolve(relative_path)
    if not os.path.isfile(file_path):
        raise ValueError('Fichier introuvable')
    size = os.path.getsize(file_path)
    if size > max_bytes:
        raise ValueError(f'Fichier trop volumineux pour l’editeur (max {format_size(max_bytes)})')
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    return {'success': True, 'name': os.path.basename(file_path), 'path': relative_path,
            'content': content, 'size': size}


def write_text_file(relative_path: str, content: str) -> dict:
    """Atomically overwrite a text file's content."""
    file_path = _resolve(relative_path)
    if not os.path.isfile(file_path):
        raise ValueError('Fichier introuvable')
    if not isinstance(content, str):
        raise ValueError('Contenu invalide')
    tmp_path = file_path + '.tmp'
    with _target_lock(file_path):
        with open(tmp_path, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
        os.replace(tmp_path, file_path)
    invalidate_after_mutation(os.path.dirname(relative_path).replace(os.sep, "/"))
    size = os.path.getsize(file_path)
    _record_history(os.path.basename(file_path), size, relative_path, action="edit")
    return {'success': True, 'name': os.path.basename(file_path), 'path': relative_path, 'size': size}


def _search_page(matches: list[dict], base_path: str, offset: int, limit: int, max_results: int) -> dict:
    total = len(matches)
    start = max(0, offset)
    page_size = max(1, min(limit, 200))
    items = matches[start:start + page_size]
    return {
        'items': items,
        'total': total,
        'has_more': start + len(items) < total,
        'truncated': total >= max_results,
        'current_path': base_path,
    }


def search_files(
    query: str,
    base_path: str = '',
    offset: int = 0,
    limit: int = 50,
    max_results: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Recursively search file names under base_path (trash excluded).

    The walk is bounded: it stops once ``max_results`` matches are collected, so
    pagination is stable only within that cap.
    """
    max_results = max_results or SEARCH_MAX_RESULTS
    query = (query or '').casefold()
    cache_key = (base_path, query, max_results)
    if len(query) < SEARCH_MIN_LENGTH:
        return {'items': [], 'total': 0, 'has_more': False, 'current_path': base_path}

    epoch = _cache_epoch()
    with _cache_guard:
        cached = _search_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < SEARCH_CACHE_TTL:
            return _search_page(cached[1], base_path, offset, limit, max_results)
        inflight_key = (*cache_key, epoch)
        inflight = _search_inflight.get(inflight_key)
        if inflight is None:
            inflight = _InflightResult()
            _search_inflight[inflight_key] = inflight
            leader = True
        else:
            leader = False
    if not leader:
        inflight.event.wait()
        if inflight.error is not None:
            raise inflight.error
        return _search_page(inflight.result or [], base_path, offset, limit, max_results)

    try:
        base = _resolve(base_path)
        matches: list[dict] = []
        scanned = 0

        def raise_walk_error(error: OSError) -> None:
            raise error

        for root, dirs, files in os.walk(base, followlinks=False, onerror=raise_walk_error):
            dirs.sort(key=str.casefold)
            root_rel = _rel(root, _mount_real())
            if _is_trash_rel(root_rel):
                dirs[:] = []
                continue
            candidates = dirs + files
            for entry_name in candidates:
                scanned += 1
                if progress is not None and scanned % 100 == 0:
                    progress(scanned, len(matches))
                if query not in entry_name.casefold():
                    continue
                full = os.path.join(root, entry_name)
                is_dir = os.path.isdir(full)
                rel = _rel(os.path.realpath(full), _mount_real())
                st = os.stat(full, follow_symlinks=False)
                size_bytes = 0 if is_dir else st.st_size
                matches.append({
                    'name': entry_name,
                    'is_dir': is_dir,
                    'path': rel,
                    'size': '' if is_dir else format_size(size_bytes),
                    'size_bytes': size_bytes,
                    'modified': int(st.st_mtime),
                    'created': int(getattr(st, 'st_ctime', 0)),
                })
                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break

        if progress is not None:
            progress(scanned, len(matches))
        inflight.result = matches
        with _cache_guard:
            if epoch == _cache_generation:
                _search_cache[cache_key] = (time.monotonic(), matches)
                _purge_cache(_search_cache, SEARCH_CACHE_MAX_ENTRIES)
        return _search_page(matches, base_path, offset, limit, max_results)
    except BaseException as exc:
        inflight.error = exc
        raise
    finally:
        with _cache_guard:
            _search_inflight.pop(inflight_key, None)
        inflight.event.set()


def get_file_properties(relative_path: str) -> dict:
    """Return detailed metadata about a file or directory."""
    target = _resolve(relative_path)
    st = os.stat(target)
    is_dir = os.path.isdir(target)
    name = os.path.basename(target) or os.path.basename(os.path.normpath(target))
    mime = None
    if not is_dir:
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    props: dict[str, str | int | bool | None] = {
        'name': name,
        'path': _rel(os.path.realpath(target), _mount_real()),
        'is_dir': is_dir,
        'size': '' if is_dir else format_size(st.st_size),
        'size_bytes': st.st_size,
        'modified': int(st.st_mtime),
        'created': int(getattr(st, 'st_ctime', 0)),
        'accessed': int(getattr(st, 'st_atime', 0)),
        'permissions': oct(st.st_mode & 0o777),
        'owner': st.st_uid,
        'group': st.st_gid,
        'mime': mime,
        'file_count': 0,
    }
    if is_dir:
        try:
            props['file_count'] = sum(1 for _ in os.scandir(target))
        except OSError:
            props['file_count'] = -1
    return props
