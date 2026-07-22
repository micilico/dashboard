from __future__ import annotations

import os
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
PUBLIC_PREFIX = os.getenv("CLOUD_PANEL_PUBLIC_PREFIX", "/cloud-panel").rstrip("/")
MOUNT_PATH = os.getenv("CLOUD_PANEL_MOUNT_PATH", "/mnt/ultra-media")
CSRF_COOKIE = "cloud_panel_csrf"
CSRF_HEADER = "X-Cloud-Panel-CSRF"
MAX_RATE_KEYS = int(os.getenv("CLOUD_PANEL_RATE_LIMIT_KEYS", "2048"))
RATE_LIMIT_CALLS = int(os.getenv("CLOUD_PANEL_RATE_LIMIT_CALLS", "40"))
RATE_LIMIT_SECONDS = int(os.getenv("CLOUD_PANEL_RATE_LIMIT_SECONDS", "60"))
CSRF_TOKEN_TTL_SECONDS = int(os.getenv("CLOUD_PANEL_CSRF_TOKEN_TTL_SECONDS", "43200"))
MAX_CSRF_TOKENS = int(os.getenv("CLOUD_PANEL_CSRF_TOKEN_KEYS", "128"))
TRUSTED_PROXY_IPS = {
    item.strip()
    for item in os.getenv("CLOUD_PANEL_TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if item.strip()
}
UPLOAD_CHUNK_SIZE = int(os.getenv("CLOUD_PANEL_UPLOAD_CHUNK_SIZE", str(1024 * 1024)))
SCANDIR_CACHE_TTL = int(os.getenv("CLOUD_PANEL_SCANDIR_CACHE_TTL", "10"))

DATA_DIR = Path(os.getenv("CLOUD_PANEL_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
DB_PATH = DATA_DIR / "cloud_panel.db"
TEMP_ZIP_DIR = DATA_DIR / "temp_zips"
SHARE_TOKEN_BYTES = int(os.getenv("CLOUD_PANEL_SHARE_TOKEN_BYTES", "24"))
SHARE_DEFAULT_EXPIRY_DAYS = int(os.getenv("CLOUD_PANEL_SHARE_DEFAULT_EXPIRY_DAYS", "7"))
SHARE_MAX_EXPIRY_DAYS = int(os.getenv("CLOUD_PANEL_SHARE_MAX_EXPIRY_DAYS", "30"))
