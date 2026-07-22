from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import DB_PATH

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _init_db(_local.conn)
    return _local.conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            is_dir INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            UNIQUE(path)
        );
        CREATE INDEX IF NOT EXISTS idx_favorites_path ON favorites(path);

        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date REAL NOT NULL,
            filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            path TEXT NOT NULL,
            token TEXT,
            action TEXT NOT NULL DEFAULT 'upload',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_date ON history(date DESC);

        CREATE TABLE IF NOT EXISTS share_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL,
            filename TEXT NOT NULL,
            is_dir INTEGER NOT NULL DEFAULT 0,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            password_hash TEXT,
            expiry_days INTEGER NOT NULL DEFAULT 7,
            expires_at REAL,
            created_at REAL NOT NULL,
            download_count INTEGER NOT NULL DEFAULT 0,
            is_revoked INTEGER NOT NULL DEFAULT 0,
            is_zip INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_share_links_token ON share_links(token);
        CREATE INDEX IF NOT EXISTS idx_share_links_expires ON share_links(expires_at);
    """)


@contextmanager
def get_db():
    conn = _get_conn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


# ── Favorites ──

def get_favorites() -> list[dict]:
    with get_db() as db:
        rows = db.execute("SELECT path, name, is_dir, created_at FROM favorites ORDER BY name ASC").fetchall()
    return [dict(r) for r in rows]


def add_favorite(path: str, name: str, is_dir: bool) -> dict:
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO favorites (path, name, is_dir, created_at) VALUES (?, ?, ?, ?)",
            (path, name, 1 if is_dir else 0, time.time()),
        )
    return {"success": True, "path": path, "name": name}


def remove_favorite(path: str) -> dict:
    with get_db() as db:
        db.execute("DELETE FROM favorites WHERE path = ?", (path,))
    return {"success": True, "path": path}


def is_favorite(path: str) -> bool:
    with get_db() as db:
        row = db.execute("SELECT 1 FROM favorites WHERE path = ?", (path,)).fetchone()
    return row is not None


# ── History ──

def add_history_entry(filename: str, size_bytes: int, path: str, token: str | None = None, action: str = "upload") -> dict:
    with get_db() as db:
        db.execute(
            "INSERT INTO history (date, filename, size_bytes, path, token, action, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), filename, size_bytes, path, token, action, time.time()),
        )
    return {"success": True}


def get_history(limit: int = 50, offset: int = 0) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT id, date, filename, size_bytes, path, token, action FROM history ORDER BY date DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Share Links ──

def create_share_link(
    path: str,
    filename: str,
    is_dir: bool,
    size_bytes: int,
    token: str,
    password_hash: str | None,
    expiry_days: int,
    is_zip: bool = False,
) -> dict:
    expires_at = time.time() + expiry_days * 86400 if expiry_days > 0 else None
    with get_db() as db:
        db.execute(
            """INSERT INTO share_links
               (token, path, filename, is_dir, size_bytes, password_hash, expiry_days, expires_at, created_at, is_zip)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (token, path, filename, 1 if is_dir else 0, size_bytes, password_hash, expiry_days, expires_at, time.time(), 1 if is_zip else 0),
        )
    return {"token": token, "path": path, "filename": filename, "expires_at": expires_at}


def get_share_link(token: str) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM share_links WHERE token = ? AND is_revoked = 0",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def get_share_links(limit: int = 50, offset: int = 0) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM share_links ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_share_link(token: str) -> dict:
    with get_db() as db:
        db.execute("UPDATE share_links SET is_revoked = 1 WHERE token = ?", (token,))
    return {"success": True, "token": token}


def extend_share_link(token: str, extra_days: int) -> dict:
    with get_db() as db:
        row = db.execute("SELECT expires_at FROM share_links WHERE token = ?", (token,)).fetchone()
        if not row:
            return {"success": False, "error": "Lien introuvable"}
        current_expiry = row["expires_at"] or time.time()
        new_expiry = current_expiry + extra_days * 86400
        db.execute("UPDATE share_links SET expires_at = ? WHERE token = ?", (new_expiry, token))
    return {"success": True, "token": token, "expires_at": new_expiry}


def increment_download_count(token: str) -> None:
    with get_db() as db:
        db.execute("UPDATE share_links SET download_count = download_count + 1 WHERE token = ?", (token,))


def get_stats() -> dict:
    with get_db() as db:
        total_links = db.execute("SELECT COUNT(*) FROM share_links").fetchone()[0]
        active_links = db.execute("SELECT COUNT(*) FROM share_links WHERE is_revoked = 0 AND (expires_at IS NULL OR expires_at > ?)", (time.time(),)).fetchone()[0]
        expired_links = db.execute("SELECT COUNT(*) FROM share_links WHERE expires_at IS NOT NULL AND expires_at <= ?", (time.time(),)).fetchone()[0]
        revoked_links = db.execute("SELECT COUNT(*) FROM share_links WHERE is_revoked = 1").fetchone()[0]
        total_downloads = db.execute("SELECT COALESCE(SUM(download_count), 0) FROM share_links").fetchone()[0]
        total_history = db.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        total_favorites = db.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]
    return {
        "total_links": total_links,
        "active_links": active_links,
        "expired_links": expired_links,
        "revoked_links": revoked_links,
        "total_downloads": total_downloads,
        "total_history": total_history,
        "total_favorites": total_favorites,
    }
