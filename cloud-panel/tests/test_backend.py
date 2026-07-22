"""Tests backend pour Cloud Panel."""

from __future__ import annotations

import os
import sys
import tempfile
import secrets
from pathlib import Path

import pytest

_dashboard_root = Path(__file__).resolve().parents[2]
if str(_dashboard_root) not in sys.path:
    sys.path.insert(0, str(_dashboard_root))

_cloud_panel_parent = Path(__file__).resolve().parent.parent
if str(_cloud_panel_parent) not in sys.path:
    sys.path.insert(0, str(_cloud_panel_parent))

from cloud_panel.security import resolve_path_within, validate_public_id
from cloud_panel.storage import format_size, clear_scandir_cache, get_cached_scandir
from cloud_panel.storage import sanitize_filename, list_directory


class TestSecurity:
    def test_resolve_path_within_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_path_within(tmpdir, "")
            assert os.path.realpath(result) == os.path.realpath(tmpdir)

    def test_resolve_path_within_subdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "sub")
            os.makedirs(sub)
            result = resolve_path_within(tmpdir, "sub")
            assert os.path.realpath(result) == os.path.realpath(sub)

    def test_resolve_path_within_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="Chemin hors"):
                resolve_path_within(tmpdir, "../")

    def test_resolve_path_within_must_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="Chemin hors"):
                resolve_path_within(tmpdir, "nonexistent", must_exist=True)

    def test_resolve_path_within_null_byte(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="Chemin invalide"):
                resolve_path_within(tmpdir, "\x00")

    def test_resolve_path_within_symlink_escaping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outside = os.path.join(tmpdir, "..", "..")
            with pytest.raises(ValueError, match="Chemin hors"):
                resolve_path_within(tmpdir, outside)

    def test_validate_public_id_valid(self):
        assert validate_public_id("abc-123_XYZ") == "abc-123_XYZ"

    def test_validate_public_id_invalid(self):
        with pytest.raises(ValueError, match="invalide"):
            validate_public_id("abc/def")

    def test_validate_public_id_empty(self):
        with pytest.raises(ValueError, match="invalide"):
            validate_public_id("")

    def test_validate_public_id_too_long(self):
        with pytest.raises(ValueError, match="invalide"):
            validate_public_id("a" * 200)


class TestSanitizeFilename:
    def test_sanitize_normal(self):
        assert sanitize_filename("hello.txt") == "hello.txt"

    def test_sanitize_with_path_separators(self):
        assert "/" not in sanitize_filename("../hello.txt")
        assert "\\" not in sanitize_filename("..\\hello.txt")

    def test_sanitize_dangerous_chars(self):
        result = sanitize_filename("a<b>c:d\"e|f?g*h")
        assert "<" not in result and ">" not in result
        assert ":" not in result and '"' not in result
        assert "|" not in result and "?" not in result
        assert "*" not in result

    def test_sanitize_empty_raises(self):
        with pytest.raises(ValueError, match="invalide"):
            sanitize_filename("")

    def test_sanitize_dot_raises(self):
        with pytest.raises(ValueError, match="invalide"):
            sanitize_filename(".")

    def test_sanitize_dotdot_raises(self):
        with pytest.raises(ValueError, match="invalide"):
            sanitize_filename("..")


class TestFormatSize:
    def test_bytes(self):
        assert format_size(500) == "500.0 B"

    def test_kb(self):
        assert "KB" in format_size(2048)

    def test_mb(self):
        assert format_size(1048576) == "1.0 MB"

    def test_gb(self):
        result = format_size(1073741824)
        assert "GB" in result

    def test_tb(self):
        result = format_size(1099511627776)
        assert "TB" in result

    def test_zero(self):
        assert format_size(0) == "0.0 B"


class TestScandirCache:
    def test_scandir_cache(self, tmp_path):
        clear_scandir_cache()
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        result = get_cached_scandir(str(tmp_path))
        assert len(result) >= 1
        names = [item["name"] for item in result]
        assert "test.txt" in names

    def test_clear_scandir_cache(self):
        clear_scandir_cache()

    def test_scandir_empty_dir(self, tmp_path):
        clear_scandir_cache()
        result = get_cached_scandir(str(tmp_path))
        assert result == []

    def test_scandir_nonexistent(self, tmp_path):
        clear_scandir_cache()
        result = get_cached_scandir(str(tmp_path / "nonexistent"))
        assert result == []


class TestListDirectory:
    def test_list_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import list_directory

        (tmp_path / "test.txt").write_text("hello")
        result = list_directory("")
        assert "items" in result
        names = [item["name"] for item in result["items"]]
        assert "test.txt" in names
        assert result["current_path"] == ""

    def test_list_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import list_directory

        with pytest.raises(ValueError, match="Chemin hors|Dossier introuvable"):
            list_directory("nonexistent")

    def test_list_subdirectory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import list_directory

        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.txt").write_text("nested")
        result = list_directory("subdir")
        names = [item["name"] for item in result["items"]]
        assert "nested.txt" in names

    def test_list_disk_usage(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import list_directory

        result = list_directory("")
        assert "disk_used" in result
        assert "disk_total" in result
        assert "disk_percent" in result


class TestCreateDirectory:
    def test_create_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import create_directory

        result = create_directory("", "newfolder")
        assert result["success"]
        assert (tmp_path / "newfolder").is_dir()

    def test_create_nested_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import create_directory

        (tmp_path / "parent").mkdir()
        result = create_directory("parent", "child")
        assert result["success"]
        assert (tmp_path / "parent" / "child").is_dir()

    def test_create_existing_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import create_directory

        (tmp_path / "exists").mkdir()
        with pytest.raises(ValueError, match="existe deja"):
            create_directory("", "exists")

    def test_create_dir_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import create_directory

        result = create_directory("", "bad/name")
        assert result["success"]
        assert (tmp_path / "bad_name").is_dir()


class TestRenameItem:
    def test_rename_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import rename_item

        (tmp_path / "old.txt").write_text("data")
        result = rename_item("", "old.txt", "new.txt")
        assert result["success"]
        assert not (tmp_path / "old.txt").exists()
        assert (tmp_path / "new.txt").exists()

    def test_rename_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import rename_item

        (tmp_path / "olddir").mkdir()
        result = rename_item("", "olddir", "newdir")
        assert result["success"]
        assert not (tmp_path / "olddir").exists()
        assert (tmp_path / "newdir").is_dir()

    def test_rename_nonexistent_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import rename_item

        with pytest.raises(ValueError, match="introuvable"):
            rename_item("", "nonexistent.txt", "new.txt")

    def test_rename_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import rename_item

        (tmp_path / "old.txt").write_text("data")
        result = rename_item("", "old.txt", "../new.txt")
        assert result["success"]
        assert (tmp_path / ".._new.txt").exists()
        os.remove(tmp_path / ".._new.txt")


class TestDeleteItem:
    def test_delete_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import delete_item

        (tmp_path / "todelete.txt").write_text("data")
        result = delete_item("", "todelete.txt")
        assert result["success"]
        assert not (tmp_path / "todelete.txt").exists()

    def test_delete_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import delete_item

        (tmp_path / "todelete").mkdir()
        (tmp_path / "todelete" / "file.txt").write_text("data")
        result = delete_item("", "todelete")
        assert result["success"]
        assert not (tmp_path / "todelete").exists()

    def test_delete_nonexistent_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import delete_item

        with pytest.raises(ValueError, match="introuvable"):
            delete_item("", "nonexistent.txt")


class TestDownloadFile:
    def test_download_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import download_file

        (tmp_path / "dl.txt").write_text("download me")
        file_path = download_file("dl.txt")
        assert file_path == os.path.realpath(str(tmp_path / "dl.txt"))

    def test_download_nonexistent_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import download_file

        with pytest.raises(ValueError, match="Chemin hors|introuvable"):
            download_file("nonexistent.txt")

    def test_download_dir_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import download_file

        (tmp_path / "adir").mkdir()
        with pytest.raises(ValueError, match="introuvable"):
            download_file("adir")


class TestUploadFileStreaming:
    @pytest.mark.asyncio
    async def test_upload_small_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO

        content = b"hello world"
        upload_file = UploadFile(filename="test.txt", file=BytesIO(content))
        result = await upload_file_streaming("", upload_file)
        assert result["success"]
        assert (tmp_path / "test.txt").read_bytes() == content

    @pytest.mark.asyncio
    async def test_upload_large_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO

        content = b"x" * (1024 * 1024 * 2)
        upload_file = UploadFile(filename="large.bin", file=BytesIO(content))
        result = await upload_file_streaming("", upload_file)
        assert result["success"]
        assert (tmp_path / "large.bin").read_bytes() == content

    @pytest.mark.asyncio
    async def test_upload_sanitizes_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO

        upload_file = UploadFile(filename="../bad.txt", file=BytesIO(b"data"))
        result = await upload_file_streaming("", upload_file)
        assert result["success"]
        assert not (tmp_path / "bad.txt").exists()

    @pytest.mark.asyncio
    async def test_upload_empty_filename_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO

        upload_file = UploadFile(filename="", file=BytesIO(b"data"))
        with pytest.raises(ValueError, match="requis"):
            await upload_file_streaming("", upload_file)

    @pytest.mark.asyncio
    async def test_upload_nonexistent_dir_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO

        upload_file = UploadFile(filename="test.txt", file=BytesIO(b"data"))
        with pytest.raises(ValueError, match="Chemin hors|introuvable"):
            await upload_file_streaming("nonexistent", upload_file)


class TestShareAndFavorites:
    def test_add_favorite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.models.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        importlib.reload(cloud_panel.models)
        from cloud_panel.models import add_favorite, get_favorites, remove_favorite, _get_conn
        _get_conn()
        add_favorite("/test/path", "test", is_dir=True)
        favs = get_favorites()
        paths = [f["path"] for f in favs]
        assert "/test/path" in paths

    def test_remove_favorite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.models.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        importlib.reload(cloud_panel.models)
        from cloud_panel.models import add_favorite, get_favorites, remove_favorite, _get_conn
        _get_conn()
        add_favorite("/remove/me", "remove", is_dir=False)
        remove_favorite("/remove/me")
        favs = get_favorites()
        paths = [f["path"] for f in favs]
        assert "/remove/me" not in paths

    def test_create_share_link(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.models.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        importlib.reload(cloud_panel.models)
        from cloud_panel.models import create_share_link, get_share_link, _get_conn
        _get_conn()
        token = "tok_" + secrets.token_hex(4)
        result = create_share_link(
            path="/test/file.txt", filename="file.txt", is_dir=False,
            size_bytes=100, token=token, password_hash=None, expiry_days=7,
        )
        link = get_share_link(token)
        assert link is not None
        assert link["filename"] == "file.txt"

    def test_revoke_share_link(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.models.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        importlib.reload(cloud_panel.models)
        from cloud_panel.models import create_share_link, revoke_share_link, get_share_link, _get_conn
        _get_conn()
        token = "rev_" + secrets.token_hex(4)
        create_share_link(
            path="/test/file2.txt", filename="file2.txt", is_dir=False,
            size_bytes=50, token=token, password_hash=None, expiry_days=7,
        )
        revoke_share_link(token)
        link = get_share_link(token)
        assert link is None

    def test_get_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.models.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        importlib.reload(cloud_panel.models)
        from cloud_panel.models import get_stats, _get_conn
        _get_conn()
        stats = get_stats()
        assert "total_links" in stats
        assert "total_favorites" in stats
        assert "total_history" in stats


class TestEdgeCases:
    def test_unicode_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import sanitize_filename
        name = sanitize_filename("café_☕_你好.txt")
        assert name == "café_☕_你好.txt"

    def test_filename_with_spaces(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import sanitize_filename
        name = sanitize_filename("my file.txt")
        assert name == "my file.txt"

    @pytest.mark.asyncio
    async def test_upload_zero_byte_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO

        upload_file = UploadFile(filename="empty.txt", file=BytesIO(b""))
        result = await upload_file_streaming("", upload_file)
        assert result["success"]
        assert (tmp_path / "empty.txt").exists()
        assert (tmp_path / "empty.txt").stat().st_size == 0

    @pytest.mark.asyncio
    async def test_upload_very_large_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO

        long_name = "a" * 200 + ".txt"
        upload_file = UploadFile(filename=long_name, file=BytesIO(b"data"))
        result = await upload_file_streaming("", upload_file)
        assert result["success"]
        assert (tmp_path / long_name).exists()

    @pytest.mark.asyncio
    async def test_concurrent_uploads_same_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO
        import asyncio

        f1 = UploadFile(filename="a.txt", file=BytesIO(b"aaa"))
        f2 = UploadFile(filename="b.txt", file=BytesIO(b"bbb"))
        results = await asyncio.gather(
            upload_file_streaming("", f1),
            upload_file_streaming("", f2),
            return_exceptions=True,
        )
        for r in results:
            assert isinstance(r, dict) and r.get("success")

    def test_rename_to_existing_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import rename_item

        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        with pytest.raises(ValueError, match="existe deja"):
            rename_item("", "a.txt", "b.txt")

    def test_delete_nonexistent_with_path_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import delete_item

        with pytest.raises(ValueError, match="introuvable|Chemin hors"):
            delete_item("", "../nonexistent")

    def test_list_directory_with_double_slash_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import list_directory

        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "file.txt").write_text("data")
        result = list_directory("sub")
        assert len(result["items"]) == 1

    def test_path_traversal_with_encoded_dots(self, tmp_path, monkeypatch):
        from cloud_panel.security import resolve_path_within
        with pytest.raises(ValueError, match="Chemin hors"):
            resolve_path_within(str(tmp_path), "..%2fetc%2f")
        with pytest.raises(ValueError, match="Chemin hors"):
            resolve_path_within(str(tmp_path), "%2e%2e%2fetc")

    def test_absolute_path_rejected(self, tmp_path, monkeypatch):
        from cloud_panel.security import resolve_path_within
        with pytest.raises(ValueError, match="Chemin hors"):
            resolve_path_within(str(tmp_path), "/etc/passwd")

    def test_symlink_outside_base_is_blocked(self, tmp_path, monkeypatch):
        from cloud_panel.security import resolve_path_within
        try:
            link = tmp_path / "link_to_outside"
            outside = Path(tempfile.mkdtemp())
            os.symlink(str(outside), str(link), target_is_directory=True)
            with pytest.raises(ValueError, match="Chemin hors"):
                resolve_path_within(str(tmp_path), "link_to_outside")
            os.rmdir(str(outside))
            os.unlink(str(link))
        except (OSError, NotImplementedError):
            pytest.skip("Les symlinks ne sont pas disponibles sur ce système")

    def test_empty_upload_streaming_file(self):
        from cloud_panel.models import add_history_entry, get_history
        add_history_entry("f.txt", 0, "/f.txt", action="upload")
        entries = get_history(limit=10)
        found = any(e["filename"] == "f.txt" for e in entries)
        assert found

    def test_format_size_pb(self):
        from cloud_panel.storage import format_size
        result = format_size(1125899906842624)
        assert "PB" in result
