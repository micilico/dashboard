from __future__ import annotations

import logging
import os
import posixpath
import re
import shutil
import time

import httpx
from fastapi import UploadFile

from .config import MOUNT_PATH, SCANDIR_CACHE_TTL, ULTRA_API_CACHE_TTL, ULTRA_API_TOKEN, ULTRA_API_URL, UPLOAD_CHUNK_SIZE
from .security import resolve_path_within

logger = logging.getLogger(__name__)

_scandir_cache: dict[tuple[str, float], tuple[float, list[dict]]] = {}
_folder_size_cache: dict[tuple[str, float], tuple[float, int]] = {}
_disk_cache: tuple[float, dict[str, str | float]] | None = None

_ULTRA_UNIT_MULTIPLIERS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
_ULTRA_PATHS = ("/get-diskquota", "/get_diskquota")


def _normalize_ultra_unit(unit: str) -> str:
    return unit.replace("B", "").replace("I", "").strip()


def _parse_ultra_quota(payload: dict) -> tuple[int, int, int] | None:
    """Parse the ultra.cc quota payload into (total, used, free) bytes."""
    if not isinstance(payload, dict):
        return None
    info = payload.get("Storage Info")
    if not isinstance(info, dict):
        info = payload.get("service_stats_info")
    if not isinstance(info, dict):
        return None
    total_value = info.get("total_storage_value")
    if total_value is None:
        return None
    try:
        total_value = int(total_value)
    except (TypeError, ValueError):
        return None
    total_unit = _normalize_ultra_unit(str(info.get("total_storage_unit") or "G").strip().upper())
    multiplier = _ULTRA_UNIT_MULTIPLIERS.get(total_unit)
    if multiplier is None:
        return None
    total_bytes = total_value * multiplier
    if total_bytes <= 0:
        return None

    free_bytes = info.get("free_storage_bytes")
    if free_bytes is not None:
        try:
            free_bytes = int(free_bytes)
        except (TypeError, ValueError):
            free_bytes = None
    if free_bytes is None:
        used_value = info.get("used_storage_value")
        if used_value is not None:
            try:
                used_value = int(used_value)
            except (TypeError, ValueError):
                used_value = None
            if used_value is not None:
                used_unit = _normalize_ultra_unit(str(info.get("used_storage_unit") or total_unit).strip().upper())
                used_multiplier = _ULTRA_UNIT_MULTIPLIERS.get(used_unit)
                if used_multiplier is not None:
                    free_bytes = total_bytes - used_value * used_multiplier
    if free_bytes is None or free_bytes < 0 or free_bytes > total_bytes:
        return None
    return total_bytes, max(0, total_bytes - free_bytes), free_bytes


def _fetch_ultra_quota_sync() -> tuple[int, int, int] | None:
    if not ULTRA_API_URL or not ULTRA_API_TOKEN:
        return None
    for path in _ULTRA_PATHS:
        try:
            with httpx.Client(timeout=httpx.Timeout(5)) as client:
                response = client.get(
                    f"{ULTRA_API_URL}{path}",
                    headers={"Authorization": f"Bearer {ULTRA_API_TOKEN}"},
                )
        except (httpx.TimeoutException, httpx.HTTPError):
            return None
        if response.status_code != 200:
            continue
        try:
            parsed = response.json()
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        totals = _parse_ultra_quota(parsed)
        if totals is not None:
            return totals
    return None


def get_disk_usage() -> dict[str, str | float]:
    """Disk usage for the sidebar. Prefers the ultra.cc quota API (user quota),
    falls back to local mount stats. Cached for ULTRA_API_CACHE_TTL seconds."""
    global _disk_cache
    now = time.time()
    if _disk_cache and now - _disk_cache[0] < ULTRA_API_CACHE_TTL:
        return _disk_cache[1]
    total_bytes = 0
    used_bytes = 0
    totals = _fetch_ultra_quota_sync()
    if totals is not None:
        total_bytes, used_bytes, _free = totals
    if total_bytes <= 0:
        try:
            usage = shutil.disk_usage(MOUNT_PATH)
            total_bytes = usage.total
            used_bytes = usage.used
        except OSError:
            total_bytes = 0
            used_bytes = 0
    if total_bytes <= 0:
        result: dict[str, str | float] = {"disk_used": "N/A", "disk_total": "N/A", "disk_percent": 0}
    else:
        result = {
            "disk_used": format_size(used_bytes),
            "disk_total": format_size(total_bytes),
            "disk_percent": round(used_bytes / total_bytes * 100, 1),
        }
    _disk_cache = (now, result)
    return result


def get_cached_scandir(path: str, ttl: int = SCANDIR_CACHE_TTL) -> list[dict]:
    """Cache scandir results for TTL seconds."""
    now = time.time()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    cache_key = (path, mtime)
    if cache_key in _scandir_cache:
        cached_time, cached_result = _scandir_cache[cache_key]
        if now - cached_time < ttl:
            return cached_result
    result = []
    try:
        for entry in os.scandir(path):
            stat = entry.stat()
            is_dir = entry.is_dir()
            size_bytes = 0 if is_dir else stat.st_size
            result.append({
                'name': entry.name,
                'is_dir': is_dir,
                'path': os.path.relpath(entry.path, MOUNT_PATH),
                'size': '' if is_dir else format_size(size_bytes),
                'size_bytes': size_bytes,
                'modified': int(stat.st_mtime),
            })
    except Exception:
        logger.warning('scandir error for %s', cache_key, exc_info=True)
    result.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    _scandir_cache[cache_key] = (now, result)
    return result


def clear_scandir_cache() -> None:
    _scandir_cache.clear()


def clear_folder_size_cache() -> None:
    _folder_size_cache.clear()


def _folder_size_cached(abs_path: str, ttl: float = SCANDIR_CACHE_TTL) -> int:
    """Recursively sum file sizes under abs_path, cached by (path, mtime)."""
    try:
        mtime = os.path.getmtime(abs_path)
    except OSError:
        mtime = 0
    cache_key = (abs_path, mtime)
    cached = _folder_size_cache.get(cache_key)
    if cached is not None and time.time() - cached[0] < ttl:
        return cached[1]
    total = 0
    try:
        for root, dirs, files in os.walk(abs_path, followlinks=False):
            dirs.sort(key=str.casefold)
            for fname in files:
                try:
                    total += os.path.getsize(os.path.join(root, fname))
                except OSError:
                    continue
    except OSError:
        logger.warning('folder size walk error for %s', abs_path, exc_info=True)
        return 0
    _folder_size_cache[cache_key] = (time.time(), total)
    return total


def get_folder_size(relative_path: str, name: str) -> dict:
    """Return the recursive size of a folder as display string + bytes."""
    parent_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(parent_dir):
        raise ValueError('Dossier introuvable')
    target = os.path.join(parent_dir, name)
    if not os.path.isdir(target):
        raise ValueError('Dossier introuvable')
    size_bytes = _folder_size_cached(os.path.realpath(target))
    return {
        'name': name,
        'size': format_size(size_bytes),
        'size_bytes': size_bytes,
        'path': os.path.relpath(target, MOUNT_PATH),
    }


def format_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def list_directory(relative_path: str = '', offset: int = 0, limit: int = 50, search: str = '') -> dict:
    """List directory contents with metadata."""
    target_dir = resolve_path_within(MOUNT_PATH, relative_path)
    if not os.path.isdir(target_dir):
        raise ValueError('Dossier introuvable')
    all_items = get_cached_scandir(target_dir)
    if search:
        query = search.casefold()
        all_items = [item for item in all_items if query in item['name'].casefold()]
    total = len(all_items)
    items = all_items[max(0, offset):max(0, offset) + max(1, min(limit, 200))]
    usage = get_disk_usage()
    disk_used = usage["disk_used"]
    disk_total = usage["disk_total"]
    disk_percent = usage["disk_percent"]
    return {
        'items': items,
        'total': total,
        'current_path': relative_path,
        'disk_used': disk_used,
        'disk_total': disk_total,
        'disk_percent': disk_percent,
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
) -> dict:
    """Upload file with streaming chunk-by-chunk, write to .tmp then atomic rename."""
    if not file.filename:
        raise ValueError('Nom de fichier requis')

    filename = sanitize_filename(file.filename)
    target_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(target_dir):
        raise ValueError('Dossier destination introuvable')

    final_path = os.path.join(target_dir, filename)
    tmp_path = final_path + '.tmp'

    try:
        total_size = 0
        with open(tmp_path, 'wb') as f:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                f.write(chunk)
                total_size += len(chunk)

        os.rename(tmp_path, final_path)
        clear_scandir_cache()
        clear_folder_size_cache()

        try:
            from .models import add_history_entry
            add_history_entry(filename, total_size, os.path.relpath(final_path, MOUNT_PATH), action="upload")
        except Exception:
            pass

        return {
            'success': True,
            'filename': filename,
            'size': format_size(total_size),
            'path': os.path.relpath(final_path, MOUNT_PATH),
        }
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        logger.exception('Upload failed')
        raise


def download_file(relative_path: str) -> str:
    """Return absolute path for file download."""
    file_path = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isfile(file_path):
        raise ValueError('Fichier introuvable')
    return file_path


def create_directory(relative_path: str, dirname: str) -> dict:
    """Create a new directory."""
    parent_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(parent_dir):
        raise ValueError('Dossier parent introuvable')

    dirname = sanitize_filename(dirname)
    new_dir = os.path.join(parent_dir, dirname)
    if os.path.exists(new_dir):
        raise ValueError('Ce dossier existe deja')

    os.makedirs(new_dir, exist_ok=True)
    clear_scandir_cache()
    clear_folder_size_cache()

    return {
        'success': True,
        'name': dirname,
        'path': os.path.relpath(new_dir, MOUNT_PATH),
    }


def rename_item(relative_path: str, old_name: str, new_name: str) -> dict:
    """Rename a file or directory."""
    parent_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    old_path = os.path.join(parent_dir, old_name)
    new_name = sanitize_filename(new_name)
    new_path = os.path.join(parent_dir, new_name)

    if not os.path.exists(old_path):
        raise ValueError('Element introuvable')
    if os.path.exists(new_path):
        raise ValueError('Ce nom existe deja')

    os.rename(old_path, new_path)
    clear_scandir_cache()
    clear_folder_size_cache()

    return {
        'success': True,
        'old_name': old_name,
        'new_name': new_name,
        'path': os.path.relpath(new_path, MOUNT_PATH),
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
    return os.path.realpath(MOUNT_PATH)


def move_item(source_path: str, name: str, dest_path: str) -> dict:
    """Move a file or directory to another folder within the mount."""
    source_dir = resolve_path_within(MOUNT_PATH, source_path, must_exist=True)
    if not os.path.isdir(source_dir):
        raise ValueError('Dossier source introuvable')
    dest_dir = resolve_path_within(MOUNT_PATH, dest_path, must_exist=True)
    if not os.path.isdir(dest_dir):
        raise ValueError('Dossier destination introuvable')

    src = resolve_path_within(MOUNT_PATH, posixpath.join(source_path, name), must_exist=True)
    dest = resolve_path_within(MOUNT_PATH, posixpath.join(dest_path, name), must_exist=False)

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
    shutil.move(src, dest)
    clear_scandir_cache()
    clear_folder_size_cache()
    new_rel = _rel(os.path.realpath(dest), mount_real)
    _rewrite_paths_after_move(old_rel, new_rel)

    try:
        from .models import add_history_entry
        size_bytes = os.path.getsize(dest) if os.path.isfile(dest) else 0
        add_history_entry(name, size_bytes, new_rel, action="move")
    except Exception:
        pass

    return {
        'success': True,
        'name': name,
        'from_path': old_rel,
        'to_path': new_rel,
    }


def delete_item(relative_path: str, name: str) -> dict:
    """Delete a file or directory."""
    parent_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    target = os.path.join(parent_dir, name)

    if not os.path.exists(target):
        raise ValueError('Element introuvable')

    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.remove(target)

    clear_scandir_cache()
    clear_folder_size_cache()

    return {
        'success': True,
        'name': name,
        'path': os.path.relpath(target, MOUNT_PATH),
    }
