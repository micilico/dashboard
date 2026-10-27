from __future__ import annotations

import os
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"


PUBLIC_PREFIX = os.getenv("CLOUD_PANEL_PUBLIC_PREFIX", "/cloud-panel").rstrip("/")
MOUNT_PATH = os.getenv("CLOUD_PANEL_MOUNT_PATH", "/mnt/ultra-media")
CSRF_COOKIE = "cloud_panel_csrf"
CSRF_HEADER = "X-Cloud-Panel-CSRF"
INTERNAL_TOKEN = os.getenv("CLOUD_PANEL_INTERNAL_TOKEN", "").strip()
MAX_RATE_KEYS = int(os.getenv("CLOUD_PANEL_RATE_LIMIT_KEYS", "2048"))
RATE_LIMIT_CALLS = int(os.getenv("CLOUD_PANEL_RATE_LIMIT_CALLS", "40"))
RATE_LIMIT_SECONDS = int(os.getenv("CLOUD_PANEL_RATE_LIMIT_SECONDS", "60"))
READ_RATE_LIMIT_CALLS = int(os.getenv("CLOUD_PANEL_READ_RATE_LIMIT_CALLS", "120"))
READ_RATE_LIMIT_SECONDS = int(os.getenv("CLOUD_PANEL_READ_RATE_LIMIT_SECONDS", "60"))
SHARE_RATE_LIMIT_CALLS = int(os.getenv("CLOUD_PANEL_SHARE_RATE_LIMIT_CALLS", "10"))
SHARE_RATE_LIMIT_SECONDS = int(os.getenv("CLOUD_PANEL_SHARE_RATE_LIMIT_SECONDS", "300"))
CSRF_TOKEN_TTL_SECONDS = int(os.getenv("CLOUD_PANEL_CSRF_TOKEN_TTL_SECONDS", "43200"))
MAX_CSRF_TOKENS = int(os.getenv("CLOUD_PANEL_CSRF_TOKEN_KEYS", "128"))
TRUSTED_PROXY_IPS = {
    item.strip()
    for item in os.getenv("CLOUD_PANEL_TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if item.strip()
}
UPLOAD_CHUNK_SIZE = int(os.getenv("CLOUD_PANEL_UPLOAD_CHUNK_SIZE", str(1024 * 1024)))
# File transfers read the (remote) mount in large blocks so a download spends
# its time on bytes instead of on thread hand-offs. DEDICATED_DOWNLOAD_STREAMS
# bounds how many transfers may occupy that pool, keeping interactive requests
# responsive while a big file is streaming.
DOWNLOAD_CHUNK_SIZE = int(os.getenv("CLOUD_PANEL_DOWNLOAD_CHUNK_SIZE", str(1024 * 1024)))
DOWNLOAD_STREAM_LIMIT = int(os.getenv("CLOUD_PANEL_DOWNLOAD_STREAM_LIMIT", "6"))
RESOLVE_CACHE_TTL = float(os.getenv("CLOUD_PANEL_RESOLVE_CACHE_TTL", "15"))
RESOLVE_CACHE_MAX_ENTRIES = int(os.getenv("CLOUD_PANEL_RESOLVE_CACHE_MAX_ENTRIES", "512"))
THUMBNAIL_WIDTH = int(os.getenv("CLOUD_PANEL_THUMBNAIL_WIDTH", "320"))
THUMBNAIL_MAX_SOURCE_BYTES = int(os.getenv("CLOUD_PANEL_THUMBNAIL_MAX_SOURCE_BYTES", str(64 * 1024 * 1024)))
THUMBNAIL_CACHE_DIR = os.getenv("CLOUD_PANEL_THUMBNAIL_CACHE_DIR", "").strip()
THUMBNAIL_CACHE_MAX_FILES = int(os.getenv("CLOUD_PANEL_THUMBNAIL_CACHE_MAX_FILES", "1024"))
THUMBNAIL_CONCURRENCY = int(os.getenv("CLOUD_PANEL_THUMBNAIL_CONCURRENCY", "3"))
THUMBNAIL_RATE_LIMIT_CALLS = int(os.getenv("CLOUD_PANEL_THUMBNAIL_RATE_LIMIT_CALLS", "600"))
THUMBNAIL_RATE_LIMIT_SECONDS = int(os.getenv("CLOUD_PANEL_THUMBNAIL_RATE_LIMIT_SECONDS", "60"))
SCANDIR_CACHE_TTL = float(os.getenv("CLOUD_PANEL_SCANDIR_CACHE_TTL", "0.5"))
SCANDIR_CACHE_MAX_ENTRIES = int(os.getenv("CLOUD_PANEL_SCANDIR_CACHE_MAX_ENTRIES", "256"))
FOLDER_SIZE_CACHE_MAX_ENTRIES = int(os.getenv("CLOUD_PANEL_FOLDER_SIZE_CACHE_MAX_ENTRIES", "128"))
PATH_LOCK_MAX_ENTRIES = int(os.getenv("CLOUD_PANEL_PATH_LOCK_MAX_ENTRIES", "256"))
DATA_DIR = Path(os.getenv("CLOUD_PANEL_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
DB_PATH = DATA_DIR / "cloud_panel.db"
TRASH_DIR_NAME = os.getenv("CLOUD_PANEL_TRASH_DIR", ".cloud-trash").strip("/")
SEARCH_MAX_RESULTS = int(os.getenv("CLOUD_PANEL_SEARCH_MAX_RESULTS", "500"))
SEARCH_MIN_LENGTH = int(os.getenv("CLOUD_PANEL_SEARCH_MIN_LENGTH", "3"))
SEARCH_CACHE_TTL = float(os.getenv("CLOUD_PANEL_SEARCH_CACHE_TTL", "5"))
SEARCH_CACHE_MAX_ENTRIES = int(os.getenv("CLOUD_PANEL_SEARCH_CACHE_MAX_ENTRIES", "64"))
TEXT_EDITOR_MAX_BYTES = int(os.getenv("CLOUD_PANEL_TEXT_EDITOR_MAX_BYTES", str(1024 * 1024)))
SHARE_TOKEN_BYTES = int(os.getenv("CLOUD_PANEL_SHARE_TOKEN_BYTES", "24"))
SHARE_DEFAULT_EXPIRY_DAYS = int(os.getenv("CLOUD_PANEL_SHARE_DEFAULT_EXPIRY_DAYS", "7"))
SHARE_MAX_EXPIRY_DAYS = int(os.getenv("CLOUD_PANEL_SHARE_MAX_EXPIRY_DAYS", "30"))
# Password unlock cookie lifetime. Folder downloads can take hours; a 10 minute
# cookie used to break them mid-transfer. 12 h matches the CSRF token lifetime.
SHARE_PASSWORD_COOKIE_SECONDS = int(os.getenv("CLOUD_PANEL_SHARE_PASSWORD_COOKIE_SECONDS", "43200"))

# Background work uses a bounded heavy queue while blocking route work keeps
# Starlette's independent interactive thread pool available.
JOB_HEAVY_WORKERS = int(os.getenv("CLOUD_PANEL_JOB_HEAVY_WORKERS", "2"))
JOB_MAX_QUEUED = int(os.getenv("CLOUD_PANEL_JOB_MAX_QUEUED", "32"))
JOB_MAX_ENTRIES = int(os.getenv("CLOUD_PANEL_JOB_MAX_ENTRIES", "256"))
JOB_STATE_PATH = Path(os.getenv("CLOUD_PANEL_JOB_STATE_PATH", str(DATA_DIR / "jobs-state.json")))

EVENTS_INTERVAL_SECONDS = float(os.getenv("CLOUD_PANEL_EVENTS_INTERVAL_SECONDS", "0.5"))
EVENTS_KEEPALIVE_SECONDS = float(os.getenv("CLOUD_PANEL_EVENTS_KEEPALIVE_SECONDS", "15"))
LIVE_RATE_LIMIT_CALLS = int(os.getenv("CLOUD_PANEL_LIVE_RATE_LIMIT_CALLS", "180"))
LIVE_RATE_LIMIT_SECONDS = int(os.getenv("CLOUD_PANEL_LIVE_RATE_LIMIT_SECONDS", "60"))
LIVE_SECTION_MAX_IDLE = int(os.getenv("CLOUD_PANEL_LIVE_SECTION_MAX_IDLE", "64"))

# Targeted rclone VFS refresh is opt-in. When enabled, the RC endpoint must be
# loopback-only so credentials and refresh controls stay backend-local.
RCLONE_REFRESH_MODE = os.getenv("CLOUD_PANEL_RCLONE_REFRESH_MODE", "off").strip().lower()
RCLONE_RC_URL = os.getenv("CLOUD_PANEL_RCLONE_RC_URL", "http://127.0.0.1:5572").rstrip("/")
RCLONE_RC_USER = os.getenv("CLOUD_PANEL_RCLONE_RC_USER", "").strip()
RCLONE_RC_PASS = os.getenv("CLOUD_PANEL_RCLONE_RC_PASS", "").strip()
