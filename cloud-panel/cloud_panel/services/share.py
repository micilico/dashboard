from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import time
import zipfile
from io import BytesIO
from pathlib import Path

from ..config import MOUNT_PATH, SHARE_TOKEN_BYTES, TEMP_ZIP_DIR
from ..models import create_share_link, increment_download_count, get_share_link
from ..security import resolve_path_within


def generate_token(data: str) -> str:
    raw = os.urandom(SHARE_TOKEN_BYTES)
    sig = hmac.new(raw, data.encode(), hashlib.sha256).hexdigest()[:12]
    return raw.hex() + sig


def create_file_share_link(
    relative_path: str,
    password_hash: str | None = None,
    expiry_days: int = 7,
) -> dict:
    file_path = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isfile(file_path):
        raise ValueError("Fichier introuvable")

    filename = os.path.basename(file_path)
    size_bytes = os.path.getsize(file_path)
    token = generate_token(relative_path)

    return create_share_link(
        path=relative_path,
        filename=filename,
        is_dir=False,
        size_bytes=size_bytes,
        token=token,
        password_hash=password_hash,
        expiry_days=expiry_days,
    )


def create_folder_share_link(
    relative_path: str,
    password_hash: str | None = None,
    expiry_days: int = 7,
) -> dict:
    dir_path = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(dir_path):
        raise ValueError("Dossier introuvable")

    filename = os.path.basename(dir_path) or "root"
    size_bytes = 0
    for root, dirs, files in os.walk(dir_path):
        for f in files:
            try:
                size_bytes += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    token = generate_token(relative_path)

    return create_share_link(
        path=relative_path,
        filename=filename,
        is_dir=True,
        size_bytes=size_bytes,
        token=token,
        password_hash=password_hash,
        expiry_days=expiry_days,
    )


def create_zip_share_link(
    relative_path: str,
    password_hash: str | None = None,
    expiry_days: int = 7,
) -> dict:
    dir_path = resolve_path_within(MOUNT_PATH, relative_path, must_exist=True)
    if not os.path.isdir(dir_path):
        raise ValueError("Dossier introuvable")

    TEMP_ZIP_DIR.mkdir(parents=True, exist_ok=True)
    token = generate_token(relative_path + "_zip")
    zip_filename = f"{token}.zip"
    zip_path = TEMP_ZIP_DIR / zip_filename

    total_size = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(dir_path):
            for f in files:
                file_path = os.path.join(root, f)
                arcname = os.path.relpath(file_path, dir_path)
                zf.write(file_path, arcname)
                try:
                    total_size += os.path.getsize(file_path)
                except OSError:
                    pass

    dir_basename = os.path.basename(dir_path) or "archive"
    zip_size = os.path.getsize(zip_path)

    result = create_share_link(
        path=relative_path,
        filename=f"{dir_basename}.zip",
        is_dir=False,
        size_bytes=total_size,
        token=token,
        password_hash=password_hash,
        expiry_days=expiry_days,
        is_zip=True,
    )
    result["zip_path"] = str(zip_path)
    result["zip_size"] = zip_size
    return result


def get_share_download_path(token: str) -> tuple[str, str]:
    link = get_share_link(token)
    if not link:
        raise ValueError("Lien introuvable")
    if link["is_revoked"]:
        raise ValueError("Ce lien a ete revoque")
    if link["expires_at"] and link["expires_at"] < time.time():
        raise ValueError("Ce lien a expire")

    increment_download_count(token)

    if link["is_zip"]:
        zip_path = TEMP_ZIP_DIR / f"{token}.zip"
        if not zip_path.exists():
            raise ValueError("Fichier ZIP introuvable")
        return str(zip_path), link["filename"]

    file_path = resolve_path_within(MOUNT_PATH, link["path"], must_exist=True)
    if not os.path.exists(file_path):
        raise ValueError("Fichier introuvable sur le disque")
    return file_path, link["filename"]


def generate_qr_data_url(token: str, base_url: str) -> str:
    """Generate a QR code PNG data URL for a share link."""
    try:
        import qrcode
        from io import BytesIO
        import base64
        qr = qrcode.make(f"{base_url}/api/download/{token}", border=1)
        buf = BytesIO()
        qr.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def cleanup_expired_zips() -> int:
    count = 0
    if not TEMP_ZIP_DIR.exists():
        return 0
    from ..models import get_share_links
    active_tokens = {
        l["token"]
        for l in get_share_links(limit=10000)
        if l.get("is_zip") and not l.get("is_revoked")
        and (l.get("expires_at") is None or l["expires_at"] > time.time())
    }
    for f in TEMP_ZIP_DIR.iterdir():
        if f.suffix != ".zip":
            continue
        token = f.stem
        if token not in active_tokens:
            f.unlink(missing_ok=True)
            count += 1
    return count
