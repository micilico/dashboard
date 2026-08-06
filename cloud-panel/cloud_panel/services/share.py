from __future__ import annotations

import os
import time

from ..config import MOUNT_PATH, PUBLIC_PREFIX, SHARE_TOKEN_BYTES, TEMP_ZIP_DIR
from ..models import create_share_link, increment_download_count, get_share_link
from ..security import resolve_path_within


def generate_token() -> str:
    """Return an opaque, non-guessable share token (SHARE_TOKEN_BYTES of entropy)."""
    return os.urandom(SHARE_TOKEN_BYTES).hex()


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
    token = generate_token()

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
    token = generate_token()

    return create_share_link(
        path=relative_path,
        filename=filename,
        is_dir=True,
        size_bytes=size_bytes,
        token=token,
        password_hash=password_hash,
        expiry_days=expiry_days,
    )


def get_share_download_path(token: str, increment: bool = True) -> tuple[str, str]:
    link = get_share_link(token)
    if not link:
        raise ValueError("Lien introuvable")
    if link["is_revoked"]:
        raise ValueError("Ce lien a ete revoque")
    if link["expires_at"] and link["expires_at"] < time.time():
        raise ValueError("Ce lien a expire")

    if increment:
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
        public_prefix = PUBLIC_PREFIX or ""
        qr = qrcode.make(f"{base_url}{public_prefix}/download/{token}", border=1)
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
