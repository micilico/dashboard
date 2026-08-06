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
from cloud_panel.storage import clear_folder_size_cache, get_folder_size
from cloud_panel.storage import sanitize_filename, list_directory
from cloud_panel.storage import _parse_ultra_quota


class TestSecurity:
    def test_route_errors_do_not_serialize_raw_exceptions(self):
        routes_dir = _cloud_panel_parent / "cloud_panel" / "routes"
        route_source = "\n".join(path.read_text() for path in routes_dir.glob("*.py"))
        assert "str(e)" not in route_source
        assert "str(exc)" not in route_source

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

    def test_sanitize_trailing_dots_and_spaces(self):
        assert sanitize_filename("file.txt ") == "file.txt"
        assert sanitize_filename("file.") == "file"
        assert sanitize_filename("file  ") == "file"

    def test_sanitize_windows_reserved_names(self):
        for reserved in ("CON", "con", "NUL", "PRN", "AUX", "COM1", "LPT9"):
            with pytest.raises(ValueError, match="invalide"):
                sanitize_filename(reserved)
            with pytest.raises(ValueError, match="invalide"):
                sanitize_filename(reserved + ".txt")


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


class TestFolderSize:
    def _reload(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import get_folder_size as fn
        return fn

    def test_folder_size_recursive(self, tmp_path, monkeypatch):
        get_folder_size_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.txt").write_bytes(b"a" * 100)
        (tmp_path / "docs" / "nested").mkdir()
        (tmp_path / "docs" / "nested" / "b.txt").write_bytes(b"b" * 200)
        result = get_folder_size_fn("", "docs")
        assert result["size_bytes"] == 300
        assert "docs" == result["name"]
        assert result["path"] == "docs"

    def test_folder_size_nested_path(self, tmp_path, monkeypatch):
        get_folder_size_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b").mkdir()
        (tmp_path / "a" / "b" / "c.txt").write_bytes(b"c" * 50)
        result = get_folder_size_fn("a", "b")
        assert result["size_bytes"] == 50

    def test_folder_size_empty(self, tmp_path, monkeypatch):
        get_folder_size_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "empty").mkdir()
        result = get_folder_size_fn("", "empty")
        assert result["size_bytes"] == 0

    def test_folder_size_missing_raises(self, tmp_path, monkeypatch):
        get_folder_size_fn = self._reload(monkeypatch, tmp_path)
        with pytest.raises(ValueError, match="Dossier introuvable"):
            get_folder_size_fn("", "nope")

    def test_folder_size_file_name_raises(self, tmp_path, monkeypatch):
        get_folder_size_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "file.txt").write_text("x")
        with pytest.raises(ValueError, match="Dossier introuvable"):
            get_folder_size_fn("", "file.txt")

    def test_folder_size_repeat_call_cached(self, tmp_path, monkeypatch):
        get_folder_size_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.txt").write_bytes(b"a" * 10)
        first = get_folder_size_fn("", "docs")
        second = get_folder_size_fn("", "docs")
        assert first["size_bytes"] == 10
        assert second["size_bytes"] == 10

    def test_folder_size_clear_cache(self, tmp_path, monkeypatch):
        get_folder_size_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.txt").write_bytes(b"a" * 10)
        get_folder_size_fn("", "docs")
        clear_folder_size_cache()
        (tmp_path / "docs" / "b.txt").write_bytes(b"b" * 5)
        assert get_folder_size_fn("", "docs")["size_bytes"] == 15


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


class TestMoveItem:
    def test_move_file_to_subdir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import move_item

        (tmp_path / "sub").mkdir()
        (tmp_path / "move.txt").write_text("data")
        result = move_item("", "move.txt", "sub")
        assert result["success"]
        assert result["to_path"] == "sub/move.txt"
        assert (tmp_path / "sub" / "move.txt").exists()
        assert not (tmp_path / "move.txt").exists()

    def test_move_dir_to_other_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import move_item

        (tmp_path / "dir").mkdir()
        (tmp_path / "dir" / "inner.txt").write_text("data")
        (tmp_path / "dst").mkdir()
        result = move_item("", "dir", "dst")
        assert result["success"]
        assert (tmp_path / "dst" / "dir" / "inner.txt").exists()
        assert not (tmp_path / "dir").exists()

    def test_move_same_location_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import move_item

        (tmp_path / "a.txt").write_text("data")
        with pytest.raises(ValueError, match="meme emplacement"):
            move_item("", "a.txt", "")

    def test_move_nonexistent_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import move_item

        with pytest.raises(ValueError, match="introuvable|Chemin hors"):
            move_item("", "nope.txt", "")

    def test_move_dir_into_itself_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import move_item

        (tmp_path / "d").mkdir()
        with pytest.raises(ValueError, match="lui-meme"):
            move_item("", "d", "d")

    def test_move_dir_into_descendant_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import move_item

        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "sub").mkdir()
        with pytest.raises(ValueError, match="lui-meme"):
            move_item("", "d", "d/sub")

    def test_move_conflict_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import move_item

        (tmp_path / "sub").mkdir()
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub" / "a.txt").write_text("y")
        with pytest.raises(ValueError, match="existe deja"):
            move_item("", "a.txt", "sub")

    def test_move_traversal_name_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import move_item

        (tmp_path / "sub").mkdir()
        outside = tmp_path.parent / f"outside_{secrets.token_hex(4)}.txt"
        outside.write_text("secret")
        with pytest.raises(ValueError, match="Chemin hors|introuvable"):
            move_item("", "../" + outside.name, "sub")
        assert outside.exists(), "file outside the mount must not be moved"

    def test_move_rewrite_ignores_similar_prefixes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        monkeypatch.setattr("cloud_panel.config.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.storage
        import cloud_panel.models
        importlib.reload(cloud_panel.storage)
        importlib.reload(cloud_panel.models)
        from cloud_panel.storage import move_item
        from cloud_panel.models import add_favorite, get_favorites, _get_conn
        _get_conn()

        (tmp_path / "report_1").mkdir()
        (tmp_path / "report_1" / "child.txt").write_text("data")
        (tmp_path / "reportX").mkdir()
        (tmp_path / "reportX" / "child.txt").write_text("data")
        (tmp_path / "arch").mkdir()
        add_favorite("report_1", "report_1", is_dir=True)
        add_favorite("report_1/child.txt", "child", is_dir=False)
        add_favorite("reportX", "reportX", is_dir=True)

        move_item("", "report_1", "arch")
        fav_paths = {f["path"] for f in get_favorites()}
        assert "arch/report_1" in fav_paths
        assert "arch/report_1/child.txt" in fav_paths
        assert "reportX" in fav_paths, "favorite with a similar prefix must not be rewritten"

    def test_move_rewrites_favorites_and_share_links(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        monkeypatch.setattr("cloud_panel.config.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.storage
        import cloud_panel.models
        importlib.reload(cloud_panel.storage)
        importlib.reload(cloud_panel.models)
        from cloud_panel.storage import move_item
        from cloud_panel.models import add_favorite, create_share_link, get_favorites, get_share_link, _get_conn
        _get_conn()

        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "report.txt").write_text("data")
        (tmp_path / "archive").mkdir()
        add_favorite("docs", "docs", is_dir=True)
        add_favorite("docs/report.txt", "report.txt", is_dir=False)
        tok = "tok_" + secrets.token_hex(4)
        create_share_link(path="docs/report.txt", filename="report.txt", is_dir=False, size_bytes=4, token=tok, password_hash=None, expiry_days=7)

        result = move_item("", "docs", "archive")
        assert result["success"]
        fav_paths = {f["path"] for f in get_favorites()}
        assert "archive/docs" in fav_paths
        assert "archive/docs/report.txt" in fav_paths
        assert get_share_link(tok)["path"] == "archive/docs/report.txt"

    def test_move_records_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        monkeypatch.setattr("cloud_panel.config.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.storage
        import cloud_panel.models
        importlib.reload(cloud_panel.storage)
        importlib.reload(cloud_panel.models)
        from cloud_panel.storage import move_item
        from cloud_panel.models import get_history, _get_conn
        _get_conn()

        (tmp_path / "sub").mkdir()
        (tmp_path / "f.txt").write_text("data")
        move_item("", "f.txt", "sub")
        moves = [h for h in get_history() if h["action"] == "move"]
        assert moves
        assert moves[0]["filename"] == "f.txt"
        assert moves[0]["path"].endswith("sub/f.txt")


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

    def test_folder_share_link_lists_only_shared_tree(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        monkeypatch.setattr("cloud_panel.models.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        import cloud_panel.services.share
        import cloud_panel.routes.share
        importlib.reload(cloud_panel.models)
        importlib.reload(cloud_panel.services.share)
        importlib.reload(cloud_panel.routes.share)
        from cloud_panel.services.share import create_folder_share_link
        from cloud_panel.routes.share import _shared_folder_listing
        from cloud_panel.models import _get_conn, get_share_link

        shared = tmp_path / "shared"
        shared.mkdir()
        (shared / "nested.txt").write_text("data")
        (tmp_path / "outside.txt").write_text("secret")
        _get_conn()

        result = create_folder_share_link("shared")
        link = get_share_link(result["token"])
        assert link is not None
        assert link["is_dir"] == 1

        listing = _shared_folder_listing(str(shared), "")
        assert [item["name"] for item in listing["items"]] == ["nested.txt"]
        with pytest.raises(ValueError, match="Chemin hors"):
            _shared_folder_listing(str(shared), "../")


class TestSharePassword:
    def test_pbkdf2_hash_roundtrip(self):
        from cloud_panel.routes.share import _hash_password, verify_password
        h = _hash_password("secret")
        assert h.startswith("pbkdf2_sha256$")
        assert verify_password("secret", h)
        assert not verify_password("wrong", h)
        assert not verify_password("secret", "")
        assert not verify_password("secret", "not-a-hash")
        assert not verify_password("secret", "pbkdf2_sha256$abc")

    def test_legacy_sha256_hash_still_verified(self):
        import hashlib
        from cloud_panel.routes.share import verify_password
        salt = "aabbccdd"
        h = salt + ":" + hashlib.sha256((salt + "oldpw").encode()).hexdigest()
        assert verify_password("oldpw", h)
        assert not verify_password("other", h)

    def test_generate_token_is_opaque_hex(self):
        from cloud_panel.services.share import generate_token
        from cloud_panel.config import SHARE_TOKEN_BYTES
        token = generate_token()
        assert len(token) == SHARE_TOKEN_BYTES * 2
        assert all(c in "0123456789abcdef" for c in token)
        assert generate_token() != generate_token()

    def test_get_share_links_hides_password_hash(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        importlib.reload(cloud_panel.models)
        from cloud_panel.models import create_share_link, get_share_links, _get_conn
        _get_conn()
        token = "tok_" + secrets.token_hex(4)
        create_share_link(
            path="/a.txt", filename="a.txt", is_dir=False, size_bytes=1,
            token=token, password_hash="pbkdf2_sha256$210000$s$h", expiry_days=7,
        )
        items = get_share_links()
        assert len(items) == 1
        assert "password_hash" not in items[0]
        assert items[0]["token"] == token

    def test_create_share_link_clamps_expiry(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        importlib.reload(cloud_panel.models)
        from cloud_panel.models import create_share_link, get_share_link, _get_conn
        from cloud_panel.config import SHARE_MAX_EXPIRY_DAYS
        _get_conn()
        token = "tok_" + secrets.token_hex(4)
        create_share_link(
            path="/b.txt", filename="b.txt", is_dir=False, size_bytes=1,
            token=token, password_hash=None, expiry_days=9999,
        )
        import time
        link = get_share_link(token)
        assert link["expires_at"] <= time.time() + SHARE_MAX_EXPIRY_DAYS * 86400 + 1

    def test_extend_share_link_clamps_to_max(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        importlib.reload(cloud_panel.models)
        from cloud_panel.models import create_share_link, extend_share_link, get_share_link, _get_conn
        from cloud_panel.config import SHARE_MAX_EXPIRY_DAYS
        _get_conn()
        token = "tok_" + secrets.token_hex(4)
        create_share_link(
            path="/c.txt", filename="c.txt", is_dir=False, size_bytes=1,
            token=token, password_hash=None, expiry_days=7,
        )
        result = extend_share_link(token, 9999)
        assert result["success"]
        import time
        assert result["expires_at"] <= time.time() + SHARE_MAX_EXPIRY_DAYS * 86400 + 1


class TestSharePasswordRateLimit:
    def test_form_render_is_free_but_wrong_passwords_are_limited(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setattr("cloud_panel.config.DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import cloud_panel.models
        import cloud_panel.services.share as share_services
        import cloud_panel.routes.share as share_routes
        importlib.reload(cloud_panel.models)
        importlib.reload(share_services)
        importlib.reload(share_routes)
        from cloud_panel.models import create_share_link, _get_conn
        from cloud_panel.routes.share import _hash_password
        from common.rate_limiter import RateLimiter

        _get_conn()
        token = "pw_" + secrets.token_hex(6)
        create_share_link(
            path="/protected.txt", filename="protected.txt", is_dir=False, size_bytes=5,
            token=token, password_hash=_hash_password("hunter2"), expiry_days=7,
        )

        import cloud_panel.main
        importlib.reload(cloud_panel.main)
        app = cloud_panel.main.app
        app.state.share_limiter = RateLimiter(max_calls=1, period_seconds=60, max_keys=64)

        from fastapi.testclient import TestClient
        client = TestClient(app)
        url = f"/cloud-panel/download/{token}"

        form = client.get(url)
        assert form.status_code == 401, "form render must not be blocked"
        wrong1 = client.get(url, params={"password": "nope"})
        assert wrong1.status_code == 403
        wrong2 = client.get(url, params={"password": "nope"})
        assert wrong2.status_code == 429, "second wrong password must be rate-limited"
        good = client.get(url, params={"password": "hunter2"}, follow_redirects=False)
        assert good.status_code == 303, "correct password must bypass the limiter"


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


class TestMediaOrganizer:
    @staticmethod
    def _reload(monkeypatch, tmp_path):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        import cloud_panel.services.media
        importlib.reload(cloud_panel.services.media)

    def test_normalize_series_name(self):
        from cloud_panel.services.media import normalize_series_name
        assert normalize_series_name("breaking.bad") == "Breaking Bad"
        assert normalize_series_name("GAME.OF.THRONES") == "Game Of Thrones"
        assert normalize_series_name("The 100") == "The 100"
        assert normalize_series_name("Better.Caul.Saul") == "Better Caul Saul"

    def test_extract_series_name_patterns(self):
        from cloud_panel.services.media import extract_series_name
        assert extract_series_name("Breaking.Bad.S01") == "Breaking.Bad"
        assert extract_series_name("Breaking.Bad.S01E01.1080p.WEB-DL") == "Breaking.Bad"
        assert extract_series_name("Breaking.Bad.Season.1") == "Breaking.Bad"
        assert extract_series_name("The Office - Saison 2") == "The Office"
        assert extract_series_name("Show.S01E03.mkv") == "Show"
        assert extract_series_name("Show.Specials") == "Show"

    def test_extract_series_name_no_match(self):
        from cloud_panel.services.media import extract_series_name
        assert extract_series_name("Movie.2020.1080p") is None
        assert extract_series_name("recap.txt") is None
        assert extract_series_name("Show.posters") is None
        assert extract_series_name("S01E01.mkv") is None
        assert extract_series_name("") is None

    def test_extract_season_number(self):
        from cloud_panel.services.media import extract_season_number, season_folder_label
        assert extract_season_number("Show.S01") == 1
        assert extract_season_number("Show.Season.2") == 2
        assert extract_season_number("Show.Saison.3") == 3
        assert extract_season_number("Show.S01E02") == 1
        assert extract_season_number("Show.Specials") == 0
        assert extract_season_number("recap.txt") is None
        assert season_folder_label(0) == "Specials"
        assert season_folder_label(2) == "Saison 2"

    def test_extract_movie(self):
        from cloud_panel.services.media import extract_movie
        assert extract_movie("Dune.2021.1080p") == {"title": "Dune", "year": "2021"}
        assert extract_movie("Inception (2010)") == {"title": "Inception", "year": "2010"}
        assert extract_movie("Dune.2021.1080p.mkv") == {"title": "Dune", "year": "2021"}
        assert extract_movie("Movie.2020") is None
        assert extract_movie("documents.2021") is None
        assert extract_movie("Breaking.Bad.S01") is None
        assert extract_movie("recap.txt") is None

    def test_detect_parasite(self):
        from cloud_panel.services.media import detect_parasite
        assert detect_parasite("sample.mkv") == "sample"
        assert detect_parasite("Show.S01E01.Sample.720p.mkv") == "sample"
        assert detect_parasite("readme.txt") == "txt"
        assert detect_parasite("ep.nfo") == "nfo"
        assert detect_parasite("Show.S01E01.mkv") is None
        assert detect_parasite("subtitles.srt") is None
        assert detect_parasite("") is None

    def test_plan_mixed_directory_without_moving(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import build_organization_plan

        (tmp_path / "Breaking.Bad.S01").mkdir()
        (tmp_path / "Breaking.Bad.S01" / "S01E01.mkv").write_text("data")
        (tmp_path / "Breaking.Bad.S01" / "sample.mkv").write_text("data")
        (tmp_path / "Breaking.Bad.S02").mkdir()
        (tmp_path / "The.Office.S01E01.1080p.mkv").write_text("data")
        (tmp_path / "Dune.2021.1080p").mkdir()
        (tmp_path / "readme.txt").write_text("x")

        plan = build_organization_plan("")
        assert plan["totals"] == {"series": 2, "series_items": 3, "movies": 1, "parasites": 2}
        by_name = {g["name"]: g for g in plan["series"]}
        assert set(by_name) == {"Breaking Bad", "The Office"}
        assert by_name["Breaking Bad"]["items"][0]["target"] == "Breaking Bad/Saison 1"
        assert by_name["The Office"]["items"][0]["target"] == "The Office/Saison 1"
        movie_targets = {m["target"] for m in plan["movies"]}
        assert movie_targets == {"Films/Dune (2021)"}
        parasite_paths = {p["path"] for p in plan["parasites"]}
        assert parasite_paths == {"Breaking.Bad.S01/sample.mkv", "readme.txt"}
        assert (tmp_path / "Breaking.Bad.S01" / "S01E01.mkv").exists(), "preview must not move anything"

    def test_apply_groups_and_renames_seasons(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.DB_PATH", Path(tmp_path) / "test.db")
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan
        from cloud_panel.models import _get_conn, get_history
        _get_conn()

        (tmp_path / "Show.S01").mkdir()
        (tmp_path / "Show.S01" / "S01E01.mkv").write_text("data")
        (tmp_path / "Show.S02").mkdir()
        (tmp_path / "Show.S02" / "ep.mkv").write_text("data")

        result = apply_organization_plan("")
        assert result["success"]
        assert result["created_series"] == ["Show"]
        assert result["series_moved"] == 2
        assert (tmp_path / "Show" / "Saison 1" / "S01E01.mkv").exists()
        assert (tmp_path / "Show" / "Saison 2" / "ep.mkv").exists()
        assert not (tmp_path / "Show.S01").exists()
        assert not (tmp_path / "Show.S02").exists()
        moves = [h for h in get_history() if h["action"] == "move"]
        assert len(moves) >= 2

    def test_apply_renames_existing_series_folder_to_normalized(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "Breaking.Bad").mkdir()
        (tmp_path / "Breaking.Bad" / "S01").mkdir()
        (tmp_path / "Breaking.Bad.S02").mkdir()

        result = apply_organization_plan("")
        assert result["renamed_series"] == ["Breaking Bad"]
        assert (tmp_path / "Breaking Bad" / "S01").is_dir()
        assert (tmp_path / "Breaking Bad" / "Saison 2").is_dir()
        assert not (tmp_path / "Breaking.Bad").exists()
        assert not (tmp_path / "Breaking.Bad.S02").exists()

    def test_apply_moves_movies_to_films_folder(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "Dune.2021.1080p").mkdir()
        (tmp_path / "Inception.2010.720p.BluRay").mkdir()

        result = apply_organization_plan("")
        assert result["movies_moved"] == 2
        assert (tmp_path / "Films" / "Dune (2021)").is_dir()
        assert (tmp_path / "Films" / "Inception (2010)").is_dir()
        assert not (tmp_path / "Dune.2021.1080p").exists()

    def test_apply_renames_movies_in_place_in_films_dir(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "films").mkdir()
        (tmp_path / "films" / "Dune.2021.1080p").mkdir()

        result = apply_organization_plan("films")
        assert result["movies_moved"] == 1
        assert (tmp_path / "films" / "Dune (2021)").is_dir()
        assert not (tmp_path / "films" / "Dune.2021.1080p").exists()

    def test_apply_reports_conflicts_without_erasing(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "Show").mkdir()
        (tmp_path / "Show" / "Saison 1").mkdir()
        (tmp_path / "Show" / "Saison 1" / "ep1.mkv").write_text("keep")
        (tmp_path / "Show.S01").mkdir()
        (tmp_path / "Show.S01" / "ep1.mkv").write_text("other")

        result = apply_organization_plan("")
        assert result["errors"], "conflict should be reported"
        assert (tmp_path / "Show" / "Saison 1" / "ep1.mkv").read_text() == "keep"
        assert (tmp_path / "Show.S01" / "ep1.mkv").exists(), "conflicted item must not be lost"

    def test_apply_leaves_parasites_in_place(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "Show.S01").mkdir()
        (tmp_path / "Show.S01" / "sample.mkv").write_text("x")
        (tmp_path / "readme.txt").write_text("x")

        result = apply_organization_plan("")
        assert (tmp_path / "Show" / "Saison 1" / "sample.mkv").exists()
        assert (tmp_path / "readme.txt").exists(), "top-level parasite must not be moved or deleted"

    def test_apply_rejects_outside_path(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan
        with pytest.raises(ValueError, match="Chemin hors|introuvable"):
            apply_organization_plan("../")


class TestUltraQuota:
    def test_parse_ultra_quota_valid(self):
        total, used, free = _parse_ultra_quota(
            {
                "service_stats_info": {
                    "free_storage_bytes": 9664750157824,
                    "total_storage_unit": "G",
                    "total_storage_value": 11176,
                    "used_storage_value": 2175,
                }
            }
        )
        assert total == 11176 * 1024**3
        assert free == 9664750157824
        assert used == total - free

    def test_parse_ultra_quota_handles_binary_units(self):
        total, _used, free = _parse_ultra_quota(
            {
                "service_stats_info": {
                    "free_storage_bytes": 1024**3,
                    "total_storage_unit": "TiB",
                    "total_storage_value": 2,
                    "used_storage_value": 1,
                }
            }
        )
        assert total == 2 * 1024**4
        assert free == 1024**3

    def test_parse_ultra_quota_storage_info_key(self):
        total, used, free = _parse_ultra_quota(
            {
                "Storage Info": {
                    "free_storage_bytes": 104152956928,
                    "total_storage_unit": "G",
                    "total_storage_value": 932,
                    "used_storage_unit": "G",
                    "used_storage_value": 835,
                }
            }
        )
        assert total == 932 * 1024**3
        assert free == 104152956928
        assert used == total - free

    def test_parse_ultra_quota_derives_free_from_used(self):
        total, used, free = _parse_ultra_quota(
            {
                "Storage Info": {
                    "total_storage_unit": "G",
                    "total_storage_value": 10,
                    "used_storage_unit": "G",
                    "used_storage_value": 4,
                }
            }
        )
        assert total == 10 * 1024**3
        assert free == 6 * 1024**3
        assert used == 4 * 1024**3

    def test_strip_ultra_suffix_normalizes_url(self):
        from cloud_panel.config import _strip_ultra_suffix

        assert _strip_ultra_suffix("https://user.host.usbx.me/ultra-api/get-diskquota") == "https://user.host.usbx.me/ultra-api"
        assert _strip_ultra_suffix("https://user.host.usbx.me/ultra-api/get_diskquota") == "https://user.host.usbx.me/ultra-api"
        assert _strip_ultra_suffix("https://user.host.usbx.me/ultra-api/") == "https://user.host.usbx.me/ultra-api"
        assert _strip_ultra_suffix("https://user.host.usbx.me/ultra-api") == "https://user.host.usbx.me/ultra-api"
        assert _strip_ultra_suffix("") == ""

    def test_parse_ultra_quota_returns_none_on_invalid_payload(self):
        assert _parse_ultra_quota({}) is None
        assert _parse_ultra_quota({"service_stats_info": {}}) is None
        assert _parse_ultra_quota({"service_stats_info": {"total_storage_value": 10}}) is None
        assert (
            _parse_ultra_quota(
                {"service_stats_info": {"total_storage_value": 10, "free_storage_bytes": "abc"}}
            )
            is None
        )
        assert (
            _parse_ultra_quota(
                {
                    "service_stats_info": {
                        "total_storage_value": 10,
                        "free_storage_bytes": 1,
                        "total_storage_unit": "X",
                    }
                }
            )
            is None
        )
        assert (
            _parse_ultra_quota(
                {
                    "service_stats_info": {
                        "total_storage_value": 10,
                        "free_storage_bytes": 20000000000,
                        "total_storage_unit": "G",
                    }
                }
            )
            is None
        )
        assert _parse_ultra_quota([]) is None
