from __future__ import annotations

import logging
import mimetypes
import os
import posixpath
import re
import shutil
import threading
import time

import httpx
from fastapi import UploadFile

from .config import MOUNT_PATH, SCANDIR_CACHE_TTL, SEARCH_MAX_RESULTS, TEXT_EDITOR_MAX_BYTES, TRASH_DIR_NAME, ULTRA_API_CACHE_TTL, ULTRA_API_TIMEOUT_SECONDS, ULTRA_API_TOKEN, ULTRA_API_URL, UPLOAD_CHUNK_SIZE
from .security import resolve_path_within

logger = logging.getLogger(__name__)

_scandir_cache: dict[tuple[str, float], tuple[float, list[dict]]] = {}
_folder_size_cache: dict[tuple[str, float], tuple[float, int]] = {}
_disk_cache: tuple[float, dict[str, str | float]] | None = None
_path_locks: dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()

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
            with httpx.Client(timeout=httpx.Timeout(ULTRA_API_TIMEOUT_SECONDS)) as client:
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


def get_disk_usage() -> dict[str, str | float | bool]:
    """Disk usage for the sidebar. Uses the ultra.cc quota API (user quota only).
    When the quota is unavailable, returns N/A instead of falling back to the
    server-wide mount stats. Cached for ULTRA_API_CACHE_TTL seconds."""
    global _disk_cache
    now = time.time()
    if _disk_cache and now - _disk_cache[0] < ULTRA_API_CACHE_TTL:
        return _disk_cache[1]
    totals = _fetch_ultra_quota_sync()
    if totals is not None:
        total_bytes, used_bytes, _free = totals
        result: dict[str, str | float | bool] = {
            "disk_used": format_size(used_bytes),
            "disk_total": format_size(total_bytes),
            "disk_percent": round(used_bytes / total_bytes * 100, 1),
            "available": True,
        }
    else:
        result = {
            "disk_used": "N/A",
            "disk_total": "N/A",
            "disk_percent": 0,
            "available": False,
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
    except Exception:
        logger.warning('scandir error for %s', cache_key, exc_info=True)
    result.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    _scandir_cache[cache_key] = (now, result)
    return result


def clear_scandir_cache() -> None:
    _scandir_cache.clear()


def clear_folder_size_cache() -> None:
    _folder_size_cache.clear()


def _target_lock(path: str) -> threading.Lock:
    """Return a per-path lock so concurrent mutations of the same target serialize."""
    real = os.path.realpath(path)
    with _path_locks_guard:
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
        'path': _rel(target, _mount_real()),
    }


def get_folder_sizes(paths: list[str]) -> dict:
    """Compute the recursive size of several folders in one batch.

    Invalid or non-directory paths are counted in ``failed`` instead of
    raising, so a single request can compute all visible folders safely.
    """
    items: list[dict] = []
    failed = 0
    for raw_rel in paths:
        rel = raw_rel.strip() if isinstance(raw_rel, str) else raw_rel
        if not rel:
            continue
        try:
            target = resolve_path_within(MOUNT_PATH, rel, must_exist=True)
            if not os.path.isdir(target):
                failed += 1
                continue
            size_bytes = _folder_size_cached(os.path.realpath(target))
            items.append({
                'path': _rel(target, _mount_real()),
                'name': rel.replace("/", os.sep).rsplit(os.sep, 1)[-1],
                'size': format_size(size_bytes),
                'size_bytes': size_bytes,
            })
        except ValueError:
            failed += 1
    return {'items': items, 'total': len(paths), 'failed': failed}


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
        'has_more': max(0, offset) + len(items) < total,
        'current_path': relative_path,
        'disk_used': disk_used,
        'disk_total': disk_total,
        'disk_percent': disk_percent,
        'disk_available': usage.get("available", True),
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
    target_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(target_dir):
        raise ValueError('Dossier destination introuvable')

    final_path = os.path.join(target_dir, filename)
    if overwrite == "rename":
        final_path = _unique_name(target_dir, filename)
    elif overwrite == "skip" and os.path.exists(final_path):
        return {
            'success': False,
            'skipped': True,
            'filename': filename,
            'path': _rel(final_path, _mount_real()),
        }

    tmp_path = final_path + '.tmp'
    try:
        total_size = 0
        with open(tmp_path, 'wb') as f:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                f.write(chunk)
                total_size += len(chunk)

        with _target_lock(final_path):
            os.replace(tmp_path, final_path)
        clear_scandir_cache()
        clear_folder_size_cache()

        _record_history(os.path.basename(final_path), total_size, _rel(final_path, _mount_real()), action="upload")

        return {
            'success': True,
            'filename': os.path.basename(final_path),
            'size': format_size(total_size),
            'path': _rel(final_path, _mount_real()),
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
        'path': _rel(new_dir, _mount_real()),
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

    with _target_lock(old_path):
        os.rename(old_path, new_path)
    clear_scandir_cache()
    clear_folder_size_cache()

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
    with _target_lock(src):
        shutil.move(src, dest)
    clear_scandir_cache()
    clear_folder_size_cache()
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
    parent_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
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

    clear_scandir_cache()
    clear_folder_size_cache()

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

    original_abs = resolve_path_within(MOUNT_PATH, entry["original_path"], must_exist=False)
    if os.path.exists(original_abs):
        original_abs = _unique_name(os.path.dirname(original_abs), os.path.basename(original_abs))

    os.makedirs(os.path.dirname(original_abs), exist_ok=True)
    with _target_lock(original_abs):
        shutil.move(trash_target, original_abs)

    remove_trash_entry(entry["id"])
    clear_scandir_cache()
    clear_folder_size_cache()

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
    clear_scandir_cache()
    clear_folder_size_cache()
    try:
        os.rmdir(trash_root)
    except OSError:
        pass
    return {'success': True, 'removed': removed}


def copy_item(relative_path: str, name: str, dest_path: str) -> dict:
    """Copy a file or directory to another folder within the mount."""
    source_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(source_dir):
        raise ValueError('Dossier source introuvable')
    dest_dir = resolve_path_within(MOUNT_PATH, dest_path, must_exist=True)
    if not os.path.isdir(dest_dir):
        raise ValueError('Dossier destination introuvable')

    src = resolve_path_within(MOUNT_PATH, posixpath.join(relative_path, name), must_exist=True)
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

    clear_scandir_cache()
    clear_folder_size_cache()

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
    parent_dir = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(parent_dir):
        raise ValueError('Dossier parent introuvable')
    filename = sanitize_filename(filename)
    target = os.path.join(parent_dir, filename)
    if os.path.exists(target):
        raise ValueError('Ce fichier existe deja')
    with _target_lock(target):
        open(target, 'w', encoding='utf-8', newline='').close()
    clear_scandir_cache()
    _record_history(filename, 0, _rel(target, _mount_real()), action="create")
    return {'success': True, 'name': filename, 'path': _rel(target, _mount_real())}


def read_text_file(relative_path: str, max_bytes: int | None = None) -> dict:
    """Return the text content of a small file."""
    max_bytes = max_bytes or TEXT_EDITOR_MAX_BYTES
    file_path = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
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
    file_path = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isfile(file_path):
        raise ValueError('Fichier introuvable')
    if not isinstance(content, str):
        raise ValueError('Contenu invalide')
    tmp_path = file_path + '.tmp'
    with _target_lock(file_path):
        with open(tmp_path, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
        os.replace(tmp_path, file_path)
    clear_scandir_cache()
    size = os.path.getsize(file_path)
    _record_history(os.path.basename(file_path), size, relative_path, action="edit")
    return {'success': True, 'name': os.path.basename(file_path), 'path': relative_path, 'size': size}


def search_files(
    query: str,
    base_path: str = '',
    offset: int = 0,
    limit: int = 50,
    max_results: int | None = None,
) -> dict:
    """Recursively search file names under base_path (trash excluded).

    The walk is bounded: it stops once ``max_results`` matches are collected, so
    pagination is stable only within that cap.
    """
    max_results = max_results or SEARCH_MAX_RESULTS
    query = (query or '').casefold()
    base = resolve_path_within(MOUNT_PATH, base_path)
    matches: list[dict] = []
    if not query:
        return {'items': [], 'total': 0, 'has_more': False, 'current_path': base_path}

    for root, dirs, files in os.walk(base, followlinks=False):
        dirs.sort(key=str.casefold)
        root_rel = _rel(root, _mount_real())
        if _is_trash_rel(root_rel):
            dirs[:] = []
            continue
        candidates = dirs + files
        for entry_name in candidates:
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

    total = len(matches)
    items = matches[max(0, offset):max(0, offset) + max(1, min(limit, 200))]
    return {
        'items': items,
        'total': total,
        'has_more': max(0, offset) + len(items) < total,
        'truncated': total >= max_results,
        'current_path': base_path,
    }


def get_file_properties(relative_path: str) -> dict:
    """Return detailed metadata about a file or directory."""
    target = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
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
