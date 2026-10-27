"""Tests backend pour Cloud Panel."""

from __future__ import annotations

import asyncio
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
from cloud_panel.storage import sanitize_filename, list_directory, directory_summary


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
        with pytest.raises(FileNotFoundError):
            get_cached_scandir(str(tmp_path / "nonexistent"))


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

    def test_inflight_folder_size_does_not_repopulate_cache_after_clear(self, tmp_path, monkeypatch):
        get_folder_size_fn = self._reload(monkeypatch, tmp_path)
        import cloud_panel.storage as storage

        (tmp_path / "docs").mkdir()
        note = tmp_path / "docs" / "a.txt"
        note.write_bytes(b"a" * 10)
        original_walk = storage.os.walk

        def walk_with_invalidation(path, *args, **kwargs):
            for entry in original_walk(path, *args, **kwargs):
                clear_folder_size_cache()
                yield entry

        monkeypatch.setattr(storage.os, "walk", walk_with_invalidation)
        assert get_folder_size_fn("", "docs")["size_bytes"] == 10

        note.write_bytes(b"a" * 30)
        assert get_folder_size_fn("", "docs")["size_bytes"] == 30

    def test_cancelled_upload_removes_temporary_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage as storage
        importlib.reload(storage)

        class CancelledUpload:
            filename = "partial.bin"
            reads = 0

            async def read(self, _size):
                self.reads += 1
                if self.reads == 1:
                    return b"partial"
                raise asyncio.CancelledError

        async def run():
            with pytest.raises(asyncio.CancelledError):
                await storage.upload_file_streaming("", CancelledUpload())

        asyncio.run(run())
        assert not (tmp_path / "partial.bin").exists()
        assert list(tmp_path.glob("*.tmp")) == []


class TestFolderSizes:
    def _reload(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import get_folder_sizes as fn
        return fn

    def test_batch_multiple(self, tmp_path, monkeypatch):
        get_folder_sizes_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.txt").write_bytes(b"a" * 100)
        (tmp_path / "media").mkdir()
        (tmp_path / "media" / "nested").mkdir()
        (tmp_path / "media" / "nested" / "b.txt").write_bytes(b"b" * 250)
        result = get_folder_sizes_fn(["docs", "media"])
        assert result["total"] == 2
        assert result["failed"] == 0
        by_path = {item["path"]: item for item in result["items"]}
        assert by_path["docs"]["size_bytes"] == 100
        assert by_path["media"]["size_bytes"] == 250

    def test_batch_invalid_path_skipped(self, tmp_path, monkeypatch):
        get_folder_sizes_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.txt").write_bytes(b"a" * 10)
        result = get_folder_sizes_fn(["docs", "nope"])
        assert result["total"] == 2
        assert result["failed"] == 1
        assert [item["path"] for item in result["items"]] == ["docs"]

    def test_batch_file_path_skipped(self, tmp_path, monkeypatch):
        get_folder_sizes_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "file.txt").write_bytes(b"f" * 5)
        result = get_folder_sizes_fn(["docs", "file.txt"])
        assert result["failed"] == 1
        assert [item["path"] for item in result["items"]] == ["docs"]

    def test_batch_matches_single(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import get_folder_size, get_folder_sizes

        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "b" / "c.txt").write_bytes(b"c" * 40)
        batch = get_folder_sizes(["a/b"])
        single = get_folder_size("a", "b")
        assert batch["items"][0]["size_bytes"] == single["size_bytes"]
        assert batch["items"][0]["path"] == single["path"]

    def test_batch_empty(self, tmp_path, monkeypatch):
        get_folder_sizes_fn = self._reload(monkeypatch, tmp_path)
        result = get_folder_sizes_fn([])
        assert result == {"items": [], "total": 0, "failed": 0}

    def test_batch_blank_entries_ignored(self, tmp_path, monkeypatch):
        get_folder_sizes_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "docs").mkdir()
        result = get_folder_sizes_fn(["", "docs", "  "])
        assert result["total"] == 3
        assert result["failed"] == 0
        assert [item["path"] for item in result["items"]] == ["docs"]

    def test_batch_reports_entries_progress(self, tmp_path, monkeypatch):
        get_folder_sizes_fn = self._reload(monkeypatch, tmp_path)
        (tmp_path / "docs").mkdir()
        for index in range(120):
            (tmp_path / "docs" / f"f{index}.txt").write_bytes(b"x")
        seen: list[tuple[int, int, int]] = []
        result = get_folder_sizes_fn(
            ["docs"],
            entries_progress=lambda index, total, entries: seen.append(
                (index, total, entries)
            ),
        )
        assert result["items"][0]["size_bytes"] == 120
        assert seen
        assert seen[-1] == (1, 1, 120)
        assert all(index == 1 and total == 1 for index, total, _ in seen)


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
        file_path, stat_result = download_file("dl.txt")
        assert file_path == os.path.realpath(str(tmp_path / "dl.txt"))
        assert stat_result.st_size == len("download me")

    def test_download_resolution_is_memoized(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import download_file

        (tmp_path / "dl.txt").write_text("download me")

        calls = {"count": 0}
        real_realpath = os.path.realpath

        def counting_realpath(path, *args, **kwargs):
            calls["count"] += 1
            return real_realpath(path, *args, **kwargs)

        monkeypatch.setattr(os.path, "realpath", counting_realpath)
        download_file("dl.txt")
        first = calls["count"]
        monkeypatch.setattr(os.path, "realpath", real_realpath)
        download_file("dl.txt")
        assert first >= 1
        # The second lookup is served from the memo: one stat, no path walk.
        assert calls["count"] == first

    def test_mutation_invalidates_resolution(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import download_file, rename_item

        (tmp_path / "dl.txt").write_text("download me")
        download_file("dl.txt")
        rename_item("", "dl.txt", "renamed.txt")
        with pytest.raises(ValueError, match="introuvable|hors du repertoire"):
            download_file("dl.txt")

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


class TestShareFolderDownload:
    """Public folder shares can download the whole tree without any archive."""

    def _app(self, tmp_path, monkeypatch):
        import importlib
        from fastapi.testclient import TestClient

        monkeypatch.setattr("cloud_panel.config.DB_PATH", Path(tmp_path) / "test.db")
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import cloud_panel.models
        import cloud_panel.services.share
        import cloud_panel.routes.share
        import cloud_panel.main

        importlib.reload(cloud_panel.models)
        importlib.reload(cloud_panel.services.share)
        importlib.reload(cloud_panel.routes.share)
        importlib.reload(cloud_panel.main)
        return TestClient(cloud_panel.main.app)

    def _seed(self, tmp_path):
        shared = Path(tmp_path) / "shared"
        (shared / "Saison 1").mkdir(parents=True)
        (shared / "Extras").mkdir()
        (shared / "note.txt").write_text("hello")
        (shared / "Saison 1" / "ep1.mkv").write_bytes(b"x" * 100)
        (shared / "Saison 1" / "ep2.mkv").write_bytes(b"y" * 200)

    def _share(self, path="shared", password_hash=None):
        from cloud_panel.models import create_share_link, _get_conn

        _get_conn()
        token = "folder_" + secrets.token_hex(6)
        create_share_link(
            path=path,
            filename=os.path.basename(path) or "root",
            is_dir=True,
            size_bytes=0,
            token=token,
            password_hash=password_hash,
            expiry_days=7,
        )
        return token

    def test_folder_page_exposes_download_controls(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        client = self._app(tmp_path, monkeypatch)
        token = self._share()

        response = client.get(f"/cloud-panel/download/{token}")

        assert response.status_code == 200
        html = response.text
        assert "Télécharger le dossier" in html
        assert 'class="btn folder-download js-download-folder"' in html
        assert 'data-folder-name="shared"' in html
        assert 'data-folder-name="Saison 1"' in html
        assert "share.js?v=" in html
        assert 'id="downloadPanel"' in html
        assert 'id="downloadCascade"' in html

    def test_listing_is_relative_and_sized(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        client = self._app(tmp_path, monkeypatch)
        token = self._share()

        response = client.get(f"/cloud-panel/download/{token}", params={"list": 1})

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["current_path"] == ""
        assert body["folder_name"] == "shared"
        items = {item["name"]: item for item in body["items"]}
        assert items["note.txt"]["path"] == "note.txt"
        assert items["note.txt"]["size_bytes"] == 5
        assert items["Saison 1"]["is_dir"] is True
        assert str(tmp_path) not in response.text

    def test_listing_is_scoped_to_a_subfolder(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        client = self._app(tmp_path, monkeypatch)
        token = self._share()

        body = client.get(
            f"/cloud-panel/download/{token}", params={"list": 1, "path": "Saison 1"}
        ).json()

        assert body["current_path"] == "Saison 1"
        assert body["folder_name"] == "Saison 1"
        assert sorted(item["name"] for item in body["items"]) == ["ep1.mkv", "ep2.mkv"]

    def test_listing_rejects_traversal(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        client = self._app(tmp_path, monkeypatch)
        token = self._share()

        response = client.get(
            f"/cloud-panel/download/{token}", params={"list": 1, "path": "../"}
        )

        assert response.status_code in (403, 404)
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] in ("share_invalid_path", "share_folder_missing")

    def test_listing_requires_the_password_cookie(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        client = self._app(tmp_path, monkeypatch)
        from cloud_panel.routes.share import _hash_password

        token = self._share(password_hash=_hash_password("secret"))

        locked = client.get(f"/cloud-panel/download/{token}", params={"list": 1})
        assert locked.status_code == 401
        assert locked.json()["error"]["code"] == "share_password_required"

        unlocked = client.get(
            f"/cloud-panel/download/{token}",
            params={"list": 1, "password": "secret"},
            follow_redirects=False,
        )
        assert unlocked.status_code == 303
        assert "Max-Age=43200" in unlocked.headers["set-cookie"]

        allowed = client.get(f"/cloud-panel/download/{token}", params={"list": 1})
        assert allowed.status_code == 200

    def test_count_flag_avoids_inflating_the_download_counter(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        client = self._app(tmp_path, monkeypatch)
        token = self._share()
        from cloud_panel.models import get_share_link

        before = get_share_link(token)["download_count"]

        first = client.get(
            f"/cloud-panel/download/{token}",
            params={"path": "note.txt", "dl": 1, "count": 0},
        )
        assert first.status_code == 200
        assert first.content == b"hello"
        assert get_share_link(token)["download_count"] == before

        second = client.get(
            f"/cloud-panel/download/{token}", params={"path": "note.txt", "dl": 1}
        )
        assert second.status_code == 200
        assert get_share_link(token)["download_count"] == before + 1

    def test_listing_flags_symlinks_for_the_downloader(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        client = self._app(tmp_path, monkeypatch)
        token = self._share()
        try:
            (Path(tmp_path) / "outside.txt").write_text("outside")
            (Path(tmp_path) / "shared" / "link.txt").symlink_to(Path(tmp_path) / "outside.txt")
            (Path(tmp_path) / "shared" / "broken").symlink_to(Path(tmp_path) / "missing.txt")
        except OSError:
            pytest.skip("symlinks are not available on this platform")

        body = client.get(f"/cloud-panel/download/{token}", params={"list": 1}).json()

        items = {item["name"]: item for item in body["items"]}
        assert items["link.txt"]["is_symlink"] is True
        assert items["note.txt"]["is_symlink"] is False
        assert items["broken"]["size_bytes"] == 0

    def test_listing_count_is_opt_out(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        client = self._app(tmp_path, monkeypatch)
        token = self._share()
        from cloud_panel.models import get_share_link

        before = get_share_link(token)["download_count"]

        client.get(f"/cloud-panel/download/{token}", params={"list": 1, "count": 0})
        assert get_share_link(token)["download_count"] == before

        client.get(f"/cloud-panel/download/{token}", params={"list": 1})
        assert get_share_link(token)["download_count"] == before + 1

    def test_file_share_rejects_listing(self, tmp_path, monkeypatch):
        (Path(tmp_path) / "file.txt").write_text("x")
        client = self._app(tmp_path, monkeypatch)
        from cloud_panel.models import create_share_link, _get_conn

        _get_conn()
        token = "file_" + secrets.token_hex(6)
        create_share_link(
            path="file.txt",
            filename="file.txt",
            is_dir=False,
            size_bytes=1,
            token=token,
            password_hash=None,
            expiry_days=7,
        )

        response = client.get(f"/cloud-panel/download/{token}", params={"list": 1})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "share_not_a_folder"


class TestInternalArrangeBatch:
    def _reload_app(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        monkeypatch.setattr("cloud_panel.config.INTERNAL_TOKEN", "secret-token")
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        import cloud_panel.routes.files as files_routes
        importlib.reload(files_routes)
        import cloud_panel.main
        importlib.reload(cloud_panel.main)
        app = cloud_panel.main.app
        app.state.action_limiter = type("L", (), {"allow": lambda self, k: True})()
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_internal_arrange_batch_renames_items(self, tmp_path, monkeypatch):
        client = self._reload_app(tmp_path, monkeypatch)
        (tmp_path / "Backrooms (2026)").mkdir()
        (tmp_path / "Backrooms (2026)" / "old.mkv").write_bytes(b"data")

        url = "/cloud-panel/api/files/internal-arrange-batch"
        no_token = client.post(url, data={"items": "[]"})
        assert no_token.status_code == 403

        items = [
            {"op": "rename", "path": "Backrooms (2026)", "old_name": "old.mkv", "new_name": "film.mkv"},
            {"op": "rename", "path": "", "old_name": "Backrooms (2026)", "new_name": "Film.2026.MULTi-SUPPLY"},
        ]
        response = client.post(
            url,
            data={"items": __import__("json").dumps(items)},
            headers={"X-Internal-Token": "secret-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["failed"] == 0
        assert (tmp_path / "Film.2026.MULTi-SUPPLY" / "film.mkv").exists()

    def test_internal_arrange_batch_reports_failures(self, tmp_path, monkeypatch):
        client = self._reload_app(tmp_path, monkeypatch)
        (tmp_path / "a.txt").write_text("a")

        items = [
            {"op": "rename", "path": "", "old_name": "a.txt", "new_name": "b.txt"},
            {"op": "rename", "path": "", "old_name": "nope.txt", "new_name": "c.txt"},
        ]
        response = client.post(
            "/cloud-panel/api/files/internal-arrange-batch",
            data={"items": __import__("json").dumps(items)},
            headers={"X-Internal-Token": "secret-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["failed"] == 1
        assert (tmp_path / "b.txt").exists()

    def test_internal_arrange_batch_mkdir_and_move(self, tmp_path, monkeypatch):
        client = self._reload_app(tmp_path, monkeypatch)
        (tmp_path / "Films").mkdir()
        (tmp_path / "Films" / "old.mkv").write_bytes(b"data")

        items = [
            {"op": "mkdir", "path": "Films", "name": "Film.2026-MULTi"},
            {"op": "move", "path": "Films", "old_name": "old.mkv", "new_name": "Film.mkv", "dest": "Films/Film.2026-MULTi"},
        ]
        response = client.post(
            "/cloud-panel/api/files/internal-arrange-batch",
            data={"items": __import__("json").dumps(items)},
            headers={"X-Internal-Token": "secret-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["failed"] == 0
        assert (tmp_path / "Films" / "Film.2026-MULTi" / "Film.mkv").exists()


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

    def test_explicit_game_detection_precedes_books_and_video(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import detect_explicit_entry

        game = tmp_path / "Game.2024.[Nintendo Switch]"
        game.mkdir()
        (game / "Game.nsp").write_bytes(b"rom")
        (game / "cinematic.mkv").write_bytes(b"video")
        (game / "readme.pdf").write_bytes(b"manual")
        book = tmp_path / "Dune.1965.epub"
        book.write_bytes(b"book")
        codex_book = tmp_path / "The.Codex.1965.epub"
        codex_book.write_bytes(b"book")
        codex_movie = tmp_path / "The.Codex.2024.1080p"
        codex_movie.mkdir()
        (codex_movie / "The.Codex.2024.1080p.mkv").write_bytes(b"video")
        ambiguous_iso = tmp_path / "Game.2024.iso"
        ambiguous_iso.write_bytes(b"iso")
        ambiguous_folder = tmp_path / "Unmarked Game"
        ambiguous_folder.mkdir()
        (ambiguous_folder / "Game.2024.iso").write_bytes(b"iso")
        (ambiguous_folder / "readme.pdf").write_bytes(b"manual")
        movie_with_booklet = tmp_path / "Movie.2024.1080p"
        movie_with_booklet.mkdir()
        (movie_with_booklet / "Movie.mkv").write_bytes(b"video")
        (movie_with_booklet / "booklet.pdf").write_bytes(b"booklet")
        book_union_names = [f"Book{suffix}" for suffix in (".kfx", ".cbt", ".m4b")]
        for name in book_union_names:
            (tmp_path / name).write_bytes(b"book")

        with os.scandir(tmp_path) as entries:
            detected = {entry.name: detect_explicit_entry(entry) for entry in entries}
        assert detected["Game.2024.[Nintendo Switch]"] == "game"
        assert detected["Dune.1965.epub"] == "book"
        assert detected["The.Codex.1965.epub"] == "book"
        assert detected["The.Codex.2024.1080p"] is None
        assert detected["Game.2024.iso"] is None
        assert detected["Unmarked Game"] is None
        assert detected["Movie.2024.1080p"] is None
        assert all(detected[name] == "book" for name in book_union_names)

    def test_weak_game_marker_requires_no_video_but_strong_signatures_win(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import build_organization_plan, detect_explicit_entry

        movie = tmp_path / "Movie.2024.PLAZA.1080p"
        movie.mkdir()
        (movie / "Movie.2024.PLAZA.1080p.mkv").write_bytes(b"video")
        (movie / "release.rar").write_bytes(b"archive")

        weak_game = tmp_path / "Game.2024.PLAZA"
        weak_game.mkdir()
        (weak_game / "setup.exe").write_bytes(b"installer")

        rom_game = tmp_path / "Cinematic.Game.PLAZA"
        rom_game.mkdir()
        (rom_game / "game.nsp").write_bytes(b"rom")
        (rom_game / "cinematic.mkv").write_bytes(b"video")

        manifest_game = tmp_path / "Steam.Game"
        manifest_game.mkdir()
        (manifest_game / "steam_api64.dll").write_bytes(b"manifest")
        (manifest_game / "cinematic.mkv").write_bytes(b"video")

        with os.scandir(tmp_path) as entries:
            detected = {entry.name: detect_explicit_entry(entry) for entry in entries}

        assert detected["Movie.2024.PLAZA.1080p"] is None
        assert detected["Game.2024.PLAZA"] == "game"
        assert detected["Cinematic.Game.PLAZA"] == "game"
        assert detected["Steam.Game"] == "game"
        assert [item["name"] for item in build_organization_plan("")["movies"]] == [
            "Movie.2024.PLAZA.1080p"
        ]

    def test_truncated_scan_skips_implicit_book_and_movie_fallback(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import (
            _ENTRY_SCAN_FILE_LIMIT,
            build_organization_plan,
            classify_explicit_entry,
        )

        game = tmp_path / "Game.2024.1080p"
        game.mkdir()
        # Sorted names make the repro deterministic: the PDF is the 4096th
        # evidence entry and the game payload is the first unread entry.
        for index in range(_ENTRY_SCAN_FILE_LIMIT - 1):
            (game / f"{index:04d}.txt").touch()
        (game / "readme.pdf").write_bytes(b"manual")
        (game / "zz-payload.bundle").write_bytes(b"payload")

        with os.scandir(game.parent) as entries:
            entry = next(candidate for candidate in entries if candidate.name == game.name)
            kind, incomplete = classify_explicit_entry(entry)
        assert kind is None
        assert incomplete is True

        plan = build_organization_plan("")
        assert plan["games"] == []
        assert plan["books"] == []
        assert plan["movies"] == []
        assert plan["series"] == []
        assert plan["canApply"] is False

    def test_truncated_scan_keeps_strong_rom_evidence(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import classify_explicit_entry

        game = tmp_path / "Large.Game"
        game.mkdir()
        (game / "0000-ROM.nsp").write_bytes(b"rom")
        (game / "zzzz-cinematic.mkv").write_bytes(b"video")

        with os.scandir(tmp_path) as entries:
            entry = next(candidate for candidate in entries if candidate.name == game.name)
            kind, incomplete = classify_explicit_entry(entry, max_entries=1)
        assert kind == "game"
        assert incomplete is False

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
        assert plan["totals"] == {"series": 2, "series_items": 3, "movies": 1, "games": 0, "books": 0, "parasites": 2, "duplicates": 0}
        by_name = {g["name"]: g for g in plan["series"]}
        assert set(by_name) == {"Breaking Bad", "The Office"}
        assert by_name["Breaking Bad"]["items"][0]["target"] == "séries/Breaking Bad/Saison 1"
        assert by_name["The Office"]["items"][0]["target"] == "séries/The Office/Saison 1"
        movie_targets = {m["target"] for m in plan["movies"]}
        assert movie_targets == {"films/Dune (2021)"}
        parasite_paths = {p["path"] for p in plan["parasites"]}
        assert parasite_paths == {"Breaking.Bad.S01/sample.mkv", "readme.txt"}
        assert (tmp_path / "Breaking.Bad.S01" / "S01E01.mkv").exists(), "preview must not move anything"

    def test_plan_routes_games_books_and_leaves_ambiguous_iso_alone(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import build_organization_plan

        game = tmp_path / "Game.Collection.[Nintendo Switch]"
        (game / "Disc 1").mkdir(parents=True)
        (game / "Disc 1" / "Game.nsp").write_bytes(b"rom")
        (game / "readme.pdf").write_bytes(b"manual")
        (tmp_path / "Dune.1965.epub").write_bytes(b"book")
        (tmp_path / "The.Codex.1965.epub").write_bytes(b"book")
        (tmp_path / "Game.2024.iso").write_bytes(b"ambiguous")
        (tmp_path / "Package.pkg").write_bytes(b"installer")

        plan = build_organization_plan("")
        assert [item["name"] for item in plan["games"]] == ["Game.Collection.[Nintendo Switch]"]
        assert [item["name"] for item in plan["books"]] == ["Dune.1965.epub", "The.Codex.1965.epub"]
        assert plan["movies"] == []
        assert plan["games"][0]["target"] == "jeux/Game.Collection.[Nintendo Switch]"
        assert plan["books"][0]["target"] == "livres/Dune.1965.epub"
        assert plan["books"][1]["target"] == "livres/The.Codex.1965.epub"

    def test_apply_preserves_game_tree_and_never_merges_existing_book_edition(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        game = tmp_path / "Game.[GOG]"
        (game / "data" / "cinematics").mkdir(parents=True)
        (game / "setup.exe").write_bytes(b"installer")
        (game / "steam_appid.txt").write_bytes(b"123")
        (game / "data" / "cinematics" / "intro.mkv").write_bytes(b"video")
        (game / "readme.pdf").write_bytes(b"manual")
        (tmp_path / "Existing.Edition.epub").write_bytes(b"new")
        (tmp_path / "livres" / "Existing.Edition.epub").mkdir(parents=True)
        (tmp_path / "livres" / "Existing.Edition.epub" / "Existing.Edition.epub").write_bytes(b"old")

        result = apply_organization_plan("")
        assert result["games_moved"] == 1
        assert (tmp_path / "jeux" / "Game.[GOG]" / "data" / "cinematics" / "intro.mkv").exists()
        assert (tmp_path / "jeux" / "Game.[GOG]" / "readme.pdf").exists()
        assert result["books_moved"] == 0
        assert (tmp_path / "Existing.Edition.epub").exists()
        assert (tmp_path / "livres" / "Existing.Edition.epub" / "Existing.Edition.epub").read_bytes() == b"old"
        assert any("fusion manuelle" in error for error in result["errors"])
        assert result["success"] is True
        assert result["completed_items"] == 1
        assert result["failed_items"] == 1

    def test_apply_reports_public_progress(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "Dune.2021.1080p.mkv").write_bytes(b"movie")
        (tmp_path / "Dune.epub").write_bytes(b"book")
        events: list[dict] = []

        result = apply_organization_plan("", on_progress=events.append)

        assert result["completed_items"] == 2
        assert result["failed_items"] == 0
        assert events
        assert events[0]["phase"] == "scanning"
        moving = [event for event in events if event["phase"] == "moving"]
        assert any(event["name"] == "Dune.2021.1080p.mkv" and event["category"] == "films" for event in moving)
        assert any(event["name"] == "Dune.epub" and event["category"] == "livres" for event in moving)
        assert all(event["total"] == 2 for event in moving)
        completed = [event["completed"] for event in moving]
        assert completed == sorted(completed)
        assert completed[-1] == 2
        assert events[-1]["phase"] == "done"
        assert events[-1]["failed"] == 0
        assert all(not str(event.get("name", "")).startswith("/") for event in events)

    def test_organize_job_factory_wires_public_progress(self, monkeypatch):
        import cloud_panel.routes.jobs as jobs_module

        def fake_apply(path, *, on_progress=None):
            assert path == ""
            on_progress({
                "phase": "moving",
                "category": "films",
                "name": "Movie.2024.mkv",
                "status": "moving",
                "completed": 1,
                "position": 2,
                "processed": 1,
                "failed": 0,
                "total": 2,
                "message": "Déplacement de Movie.2024.mkv (2/2)",
            })
            on_progress({
                "phase": "done",
                "completed": 2,
                "processed": 2,
                "failed": 0,
                "total": 2,
                "message": "Rangement terminé.",
            })
            return {"success": True}

        monkeypatch.setattr(jobs_module, "is_qbittorrent_tree", lambda _path: False)
        monkeypatch.setattr(jobs_module, "apply_organization_plan", fake_apply)

        class Context:
            def __init__(self):
                self.reports = []

            def report(self, **payload):
                self.reports.append(payload)

        context = Context()
        factory = jobs_module._build_factory("organize_apply", {"path": ""})
        assert factory(context) == {"success": True}
        assert context.reports[0]["message"] == "Préparation du rangement"
        moving = context.reports[1]
        assert moving["progress"] == 1
        assert moving["total"] == 2
        assert moving["data"]["name"] == "Movie.2024.mkv"
        assert moving["data"]["category"] == "films"
        assert "/" not in moving["data"]["name"]
        assert context.reports[-1]["data"]["phase"] == "done"

    def test_game_and_book_destination_collisions_are_visible_and_not_ready(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan, build_organization_plan

        (tmp_path / "jeux" / "Game.[GOG]").mkdir(parents=True)
        (tmp_path / "jeux" / "Game.[GOG]" / "keep.bin").write_bytes(b"existing")
        (tmp_path / "Game.[GOG]").mkdir()
        (tmp_path / "Game.[GOG]" / "setup.exe").write_bytes(b"source")

        (tmp_path / "livres" / "Dune.epub").mkdir(parents=True)
        (tmp_path / "livres" / "Dune.epub" / "keep.epub").write_bytes(b"existing")
        (tmp_path / "Dune.epub").write_bytes(b"source")

        plan = build_organization_plan("")
        assert plan["status"] == "conflict"
        assert plan["canApply"] is False
        assert {item["status"] for item in [*plan["games"], *plan["books"]]} == {"conflict"}
        assert {conflict["code"] for conflict in plan["conflicts"]} == {"destination_exists"}
        assert all(item["conflict"]["message"] for item in [*plan["games"], *plan["books"]])
        assert all(item["conflict"]["recovery"] for item in [*plan["games"], *plan["books"]])

        result = apply_organization_plan("")
        assert result["success"] is False
        assert result["completed_items"] == 0
        assert result["failed_items"] == 2
        assert result["games_moved"] == 0
        assert result["books_moved"] == 0
        assert (tmp_path / "Game.[GOG]" / "setup.exe").exists()
        assert (tmp_path / "Dune.epub").exists()

    def test_organizer_ignores_top_level_and_nested_symlinks(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import _find_category_dir, build_organization_plan

        with tempfile.TemporaryDirectory(prefix="cloud-organizer-outside-") as outside_dir:
            outside = Path(outside_dir)
            (outside / "Hidden.nsp").write_bytes(b"rom")
            os.symlink(outside, tmp_path / "External.Game", target_is_directory=True)
            os.symlink(outside, tmp_path / "jeux", target_is_directory=True)

            book = tmp_path / "Book.with.links"
            book.mkdir()
            (book / "readme.pdf").write_bytes(b"manual")
            os.symlink(outside / "Hidden.nsp", book / "linked.nsp")
            os.symlink(outside, book / "linked-game", target_is_directory=True)

            assert _find_category_dir(str(tmp_path), {"jeux"}) is None
            plan = build_organization_plan("")
            assert plan["games"] == []
            assert [item["name"] for item in plan["books"]] == ["Book.with.links"]
            assert (outside / "Hidden.nsp").read_bytes() == b"rom"

    def test_in_place_game_category_does_not_report_game_readmes_as_parasites(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import build_organization_plan

        (tmp_path / "jeux" / "Game").mkdir(parents=True)
        (tmp_path / "jeux" / "Game" / "readme.txt").write_text("manual", encoding="utf-8")
        (tmp_path / "jeux" / "readme.txt").write_text("manual", encoding="utf-8")

        plan = build_organization_plan("jeux")
        assert plan["games"] == []
        assert plan["parasites"] == []

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
        assert (tmp_path / "séries" / "Show" / "Saison 1" / "S01E01.mkv").exists()
        assert (tmp_path / "séries" / "Show" / "Saison 2" / "ep.mkv").exists()
        assert not (tmp_path / "Show.S01").exists()
        assert not (tmp_path / "Show.S02").exists()
        moves = [h for h in get_history() if h["action"] == "move"]
        assert len(moves) >= 2

    def test_apply_renames_existing_series_folder_to_normalized(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "séries").mkdir()
        (tmp_path / "séries" / "Breaking.Bad").mkdir()
        (tmp_path / "séries" / "Breaking.Bad" / "S01").mkdir()
        (tmp_path / "Breaking.Bad.S02").mkdir()

        result = apply_organization_plan("")
        assert result["renamed_series"] == ["Breaking Bad"]
        assert (tmp_path / "séries" / "Breaking Bad" / "S01").is_dir()
        assert (tmp_path / "séries" / "Breaking Bad" / "Saison 2").is_dir()
        assert not (tmp_path / "séries" / "Breaking.Bad").exists()
        assert not (tmp_path / "Breaking.Bad.S02").exists()

    def test_apply_moves_movies_to_films_folder(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "Dune.2021.1080p").mkdir()
        (tmp_path / "Inception.2010.720p.BluRay").mkdir()

        result = apply_organization_plan("")
        assert result["movies_moved"] == 2
        assert (tmp_path / "films" / "Dune (2021)").is_dir()
        assert (tmp_path / "films" / "Inception (2010)").is_dir()
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

        (tmp_path / "séries").mkdir()
        (tmp_path / "séries" / "Show").mkdir()
        (tmp_path / "séries" / "Show" / "Saison 1").mkdir()
        (tmp_path / "séries" / "Show" / "Saison 1" / "ep1.mkv").write_text("keep")
        (tmp_path / "Show.S01").mkdir()
        (tmp_path / "Show.S01" / "ep1.mkv").write_text("other")

        result = apply_organization_plan("")
        assert result["errors"], "conflict should be reported"
        assert (tmp_path / "séries" / "Show" / "Saison 1" / "ep1.mkv").read_text() == "keep"
        assert (tmp_path / "Show.S01" / "ep1.mkv").exists(), "conflicted item must not be lost"

    def test_apply_leaves_parasites_in_place(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "Show.S01").mkdir()
        (tmp_path / "Show.S01" / "sample.mkv").write_text("x")
        (tmp_path / "readme.txt").write_text("x")

        result = apply_organization_plan("")
        assert (tmp_path / "séries" / "Show" / "Saison 1" / "sample.mkv").exists()
        assert (tmp_path / "readme.txt").exists(), "top-level parasite must not be moved or deleted"

    def test_apply_routes_media_to_existing_categories(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "films").mkdir()
        (tmp_path / "séries").mkdir()
        (tmp_path / "Show.S01").mkdir()
        (tmp_path / "Movie.2024.1080p").mkdir()

        result = apply_organization_plan("")
        assert result["series_moved"] == 1
        assert result["movies_moved"] == 1
        assert (tmp_path / "séries" / "Show" / "Saison 1").is_dir()
        assert (tmp_path / "films" / "Movie (2024)").is_dir()

    def test_apply_skips_identical_duplicate_files(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "séries" / "Show" / "Saison 1").mkdir(parents=True)
        (tmp_path / "séries" / "Show" / "Saison 1" / "episode.mkv").write_text("same")
        (tmp_path / "Show.S01").mkdir()
        (tmp_path / "Show.S01" / "episode.mkv").write_text("same")

        result = apply_organization_plan("")
        assert result["duplicates_skipped"] == 1
        assert not (tmp_path / "Show.S01").exists()
        assert (tmp_path / "séries" / "Show" / "Saison 1" / "episode.mkv").read_text() == "same"

    def test_apply_merges_into_existing_noncanonical_season_dir(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "séries" / "Show" / "S1").mkdir(parents=True)
        (tmp_path / "séries" / "Show" / "S1" / "S01E01.mkv").write_text("data")
        (tmp_path / "Show.S02").mkdir()
        (tmp_path / "Show.S02" / "S02E01.mkv").write_text("data")

        result = apply_organization_plan("")
        assert result["success"]
        assert (tmp_path / "séries" / "Show" / "S1" / "S01E01.mkv").exists()
        assert (tmp_path / "séries" / "Show" / "Saison 2" / "S02E01.mkv").exists()
        assert not (tmp_path / "séries" / "Show" / "Saison 1").exists(), "must reuse existing S1, not create Saison 1"
        assert not (tmp_path / "Show.S02").exists()

    def test_apply_places_loose_episode_in_existing_noncanonical_season(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "séries" / "Show" / "Season 1").mkdir(parents=True)
        (tmp_path / "Show.S01E02.mkv").write_text("data")

        result = apply_organization_plan("")
        assert result["success"]
        assert (tmp_path / "séries" / "Show" / "Season 1" / "Show.S01E02.mkv").exists()
        assert not (tmp_path / "séries" / "Show" / "Saison 1").exists(), "must reuse existing Season 1 folder"
        assert not (tmp_path / "Show.S01E02.mkv").exists()

    def test_plan_preview_targets_existing_season_folder(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import build_organization_plan

        (tmp_path / "séries" / "Show" / "S1").mkdir(parents=True)
        (tmp_path / "Show.S01").mkdir()
        (tmp_path / "Show.S01" / "S01E02.mkv").write_text("data")

        plan = build_organization_plan("")
        item = plan["series"][0]["items"][0]
        assert item["target"] == "séries/Show/S1"

    def test_apply_skips_episode_similar_file_without_moving(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "séries" / "Show" / "Saison 1").mkdir(parents=True)
        (tmp_path / "séries" / "Show" / "Saison 1" / "Show.S01E01.720p.mkv").write_text("existing")
        (tmp_path / "Show.S01E01.1080p.mkv").write_text("source")

        result = apply_organization_plan("")
        assert result["errors"], "episode-similar file should be reported"
        assert result["duplicates_skipped"] == 1
        assert (tmp_path / "Show.S01E01.1080p.mkv").exists(), "source must be left in place"
        assert not (tmp_path / "séries" / "Show" / "Saison 1" / "Show.S01E01.1080p.mkv").exists()
        assert (tmp_path / "séries" / "Show" / "Saison 1" / "Show.S01E01.720p.mkv").exists()

    def test_apply_merges_movie_into_existing_folder(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "films").mkdir()
        (tmp_path / "films" / "Dune 2021").mkdir()
        (tmp_path / "films" / "Dune 2021" / "dune.mkv").write_text("data")
        (tmp_path / "Dune.2021.1080p").mkdir()
        (tmp_path / "Dune.2021.1080p" / "extra.mkv").write_text("more")

        result = apply_organization_plan("")
        assert result["movies_moved"] == 1
        assert (tmp_path / "films" / "Dune 2021" / "dune.mkv").exists()
        assert (tmp_path / "films" / "Dune 2021" / "extra.mkv").exists()
        assert not (tmp_path / "films" / "Dune (2021)").exists(), "must reuse existing folder"
        assert not (tmp_path / "Dune.2021.1080p").exists()

    def test_real_qbittorrent_tree_uses_nested_media_destinations(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan, build_organization_plan

        source = tmp_path / "qbittorrent"
        (source / "Dune.2021.1080p").mkdir(parents=True)
        (source / "Dune.2021.1080p" / "Dune.mkv").write_text("movie")
        (source / "Show.S01").mkdir()
        (source / "Show.S01" / "Show.S01E01.mkv").write_text("episode")

        plan = build_organization_plan("qbittorrent")
        assert {item["target"] for item in plan["movies"]} == {"qbittorrent/Films/Dune (2021)"}
        assert plan["series"][0]["items"][0]["target"] == "qbittorrent/Series/Show/Saison 1"
        apply_organization_plan("qbittorrent")
        assert (source / "Films" / "Dune (2021)").is_dir()
        assert (source / "Series" / "Show" / "Saison 1" / "Show.S01E01.mkv").exists()
        assert not (tmp_path / "films").exists()
        assert not (tmp_path / "séries").exists()

    def test_same_size_different_content_is_not_deleted(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan

        (tmp_path / "qbittorrent" / "Films" / "Dune (2021)").mkdir(parents=True)
        (tmp_path / "qbittorrent" / "Films" / "Dune (2021)" / "Dune.mkv").write_bytes(b"keep")
        (tmp_path / "qbittorrent" / "Dune.2021.1080p").mkdir()
        (tmp_path / "qbittorrent" / "Dune.2021.1080p" / "Dune.mkv").write_bytes(b"drop")

        result = apply_organization_plan("qbittorrent")
        assert result["errors"]
        assert (tmp_path / "qbittorrent" / "Dune.2021.1080p" / "Dune.mkv").exists()

    def test_qbittorrent_plan_does_not_scan_nested_categories(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import build_organization_plan

        (tmp_path / "qbittorrent" / "Films" / "Nested.Movie.2024.1080p").mkdir(parents=True)
        (tmp_path / "qbittorrent" / "Series" / "Nested.Show.S01").mkdir(parents=True)
        (tmp_path / "qbittorrent" / "Musique" / "Album.S01").mkdir(parents=True)
        (tmp_path / "qbittorrent" / "Jeux" / "Game.2024.1080p").mkdir(parents=True)

        plan = build_organization_plan("qbittorrent")
        assert plan["series"] == []
        assert plan["movies"] == []

    def test_apply_rejects_outside_path(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        from cloud_panel.services.media import apply_organization_plan
        with pytest.raises(ValueError, match="Chemin hors|introuvable"):
            apply_organization_plan("../")

    def test_qbittorrent_tree_is_rejected_by_browser_organizer(self, tmp_path, monkeypatch):
        self._reload(monkeypatch, tmp_path)
        (tmp_path / "qbittorrent").mkdir()
        import importlib
        import cloud_panel.routes.files as files_routes
        import cloud_panel.main

        importlib.reload(files_routes)
        importlib.reload(cloud_panel.main)
        app = cloud_panel.main.app
        app.state.action_limiter = type("L", (), {"allow": lambda self, key: True})()
        from fastapi.testclient import TestClient

        client = TestClient(app)
        session = client.get("/cloud-panel/api/session").json()
        response = client.post(
            "/cloud-panel/api/files/organize/preview",
            data={"path": "qbittorrent"},
            headers={"X-Cloud-Panel-CSRF": session["csrfToken"]},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "qbit_organize_coordinated"


class TestCopyItem:
    def _reload(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import copy_item
        return copy_item

    def test_copy_file_cross_dir(self, tmp_path, monkeypatch):
        copy_item = self._reload(tmp_path, monkeypatch)
        (tmp_path / "src").mkdir()
        (tmp_path / "dst").mkdir()
        (tmp_path / "src" / "a.txt").write_text("data")
        result = copy_item("src", "a.txt", "dst")
        assert result["success"]
        assert (tmp_path / "dst" / "a.txt").read_text() == "data"
        assert (tmp_path / "src" / "a.txt").exists()

    def test_copy_dir_cross_dir(self, tmp_path, monkeypatch):
        copy_item = self._reload(tmp_path, monkeypatch)
        (tmp_path / "src").mkdir()
        (tmp_path / "dst").mkdir()
        (tmp_path / "src" / "folder").mkdir()
        (tmp_path / "src" / "folder" / "f.txt").write_text("x")
        copy_item("src", "folder", "dst")
        assert (tmp_path / "dst" / "folder" / "f.txt").read_text() == "x"

    def test_duplicate_same_dir_suffixes(self, tmp_path, monkeypatch):
        copy_item = self._reload(tmp_path, monkeypatch)
        (tmp_path / "a.txt").write_text("data")
        first = copy_item("", "a.txt", "")
        assert first["name"] == "a (copie).txt"
        second = copy_item("", "a.txt", "")
        assert second["name"] == "a (copie 2).txt"

    def test_copy_dir_into_itself_raises(self, tmp_path, monkeypatch):
        copy_item = self._reload(tmp_path, monkeypatch)
        (tmp_path / "folder").mkdir()
        with pytest.raises(ValueError):
            copy_item("", "folder", "folder")


class TestTrash:
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        monkeypatch.setattr("cloud_panel.config.DATA_DIR", Path(tmp_path))
        monkeypatch.setattr("cloud_panel.config.DB_PATH", Path(tmp_path) / "test.db")
        monkeypatch.setattr("cloud_panel.models.DB_PATH", Path(tmp_path) / "test.db")
        import importlib
        import cloud_panel.models
        import cloud_panel.storage
        importlib.reload(cloud_panel.models)
        importlib.reload(cloud_panel.storage)
        from cloud_panel.models import _get_conn
        from cloud_panel.storage import delete_item, restore_item, empty_trash, list_trash
        _get_conn()
        return delete_item, restore_item, empty_trash, list_trash

    def test_delete_moves_to_trash(self, tmp_path, monkeypatch):
        delete_item, _, _, list_trash = self._setup(tmp_path, monkeypatch)
        (tmp_path / "gone.txt").write_text("data")
        result = delete_item("", "gone.txt")
        assert result["success"]
        assert result["trashed"]
        assert not (tmp_path / "gone.txt").exists()
        assert (tmp_path / ".cloud-trash" / "gone.txt").exists()
        items = list_trash()
        assert any(i["name"] == "gone.txt" for i in items)

    def test_permanent_delete_skips_trash(self, tmp_path, monkeypatch):
        delete_item, _, _, list_trash = self._setup(tmp_path, monkeypatch)
        (tmp_path / "gone.txt").write_text("data")
        delete_item("", "gone.txt", permanent=True)
        assert not (tmp_path / "gone.txt").exists()
        assert not (tmp_path / ".cloud-trash").exists()
        assert list_trash() == []

    def test_restore_returns_file(self, tmp_path, monkeypatch):
        delete_item, restore_item, _, _ = self._setup(tmp_path, monkeypatch)
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "f.txt").write_text("data")
        delete_item("sub", "f.txt")
        result = restore_item("sub/f.txt")
        assert result["success"]
        assert (tmp_path / "sub" / "f.txt").read_text() == "data"
        assert not (tmp_path / ".cloud-trash" / "sub" / "f.txt").exists()

    def test_restore_conflict_suffixes(self, tmp_path, monkeypatch):
        delete_item, restore_item, _, _ = self._setup(tmp_path, monkeypatch)
        (tmp_path / "f.txt").write_text("old")
        delete_item("", "f.txt")
        (tmp_path / "f.txt").write_text("new")
        result = restore_item("f.txt")
        assert result["path"].endswith("f (copie).txt")
        assert (tmp_path / "f.txt").read_text() == "new"

    def test_empty_trash_purges(self, tmp_path, monkeypatch):
        delete_item, _, empty_trash, list_trash = self._setup(tmp_path, monkeypatch)
        (tmp_path / "a.txt").write_text("data")
        (tmp_path / "b.txt").write_text("data")
        delete_item("", "a.txt")
        delete_item("", "b.txt")
        result = empty_trash()
        assert result["removed"] == 2
        assert list_trash() == []
        assert not (tmp_path / ".cloud-trash").exists()


class TestSearchFiles:
    def test_search_recursive(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import search_files
        (tmp_path / "Series").mkdir()
        (tmp_path / "Series" / "MyShow.S01").mkdir()
        (tmp_path / "Series" / "MyShow.S01" / "ep.mkv").write_text("x")
        (tmp_path / "films.txt").write_text("x")
        result = search_files("myshow", "", limit=50)
        assert result["total"] == 1
        assert result["items"][0]["name"] == "MyShow.S01"

    def test_search_excludes_trash(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import search_files
        (tmp_path / ".cloud-trash").mkdir()
        (tmp_path / ".cloud-trash" / "secret.txt").write_text("x")
        (tmp_path / "visible.txt").write_text("x")
        result = search_files("secret", "", limit=50)
        assert result["total"] == 0
        assert search_files("visible", "", limit=50)["total"] == 1

    def test_search_empty_query(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import search_files
        assert search_files("", "", limit=50)["total"] == 0

    def test_search_pages_reuse_one_recursive_walk(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage as storage
        importlib.reload(storage)

        for index in range(25):
            (tmp_path / f"match-{index:02d}.txt").write_text("x")
        original_walk = storage.os.walk
        walks = 0

        def counting_walk(*args, **kwargs):
            nonlocal walks
            walks += 1
            return original_walk(*args, **kwargs)

        monkeypatch.setattr(storage.os, "walk", counting_walk)
        first = storage.search_files("match", "", offset=0, limit=10)
        second = storage.search_files("match", "", offset=10, limit=10)

        assert len(first["items"]) == 10
        assert len(second["items"]) == 10
        assert {item["name"] for item in first["items"]}.isdisjoint(
            {item["name"] for item in second["items"]}
        )
        assert walks == 1


class TestTextFiles:
    def _reload(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import create_text_file, read_text_file, write_text_file
        return create_text_file, read_text_file, write_text_file

    def test_create_and_write_roundtrip(self, tmp_path, monkeypatch):
        create_text_file, read_text_file, write_text_file = self._reload(tmp_path, monkeypatch)
        create_text_file("", "note.txt")
        assert (tmp_path / "note.txt").exists()
        write_text_file("note.txt", "bonjour\nmonde")
        result = read_text_file("note.txt")
        assert result["content"] == "bonjour\nmonde"
        assert result["name"] == "note.txt"

    def test_create_existing_raises(self, tmp_path, monkeypatch):
        create_text_file, _, _ = self._reload(tmp_path, monkeypatch)
        (tmp_path / "note.txt").write_text("x")
        with pytest.raises(ValueError):
            create_text_file("", "note.txt")

    def test_read_too_large_raises(self, tmp_path, monkeypatch):
        _, read_text_file, _ = self._reload(tmp_path, monkeypatch)
        (tmp_path / "big.txt").write_text("x" * 100)
        with pytest.raises(ValueError):
            read_text_file("big.txt", max_bytes=8)

    def test_write_invalidates_cached_parent_size(self, tmp_path, monkeypatch):
        _, _, write_text_file = self._reload(tmp_path, monkeypatch)
        from cloud_panel.storage import get_folder_size
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "note.txt").write_text("x" * 10)
        assert get_folder_size("", "docs")["size_bytes"] == 10
        write_text_file("docs/note.txt", "x" * 30)
        assert get_folder_size("", "docs")["size_bytes"] == 30


class TestUploadConflict:
    @pytest.mark.asyncio
    async def test_upload_skip_keeps_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO
        (tmp_path / "a.txt").write_text("old")
        result = await upload_file_streaming("", UploadFile(filename="a.txt", file=BytesIO(b"new")), overwrite="skip")
        assert not result["success"]
        assert result["skipped"]
        assert (tmp_path / "a.txt").read_text() == "old"

    @pytest.mark.asyncio
    async def test_upload_overwrite_replaces(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO
        (tmp_path / "a.txt").write_text("old")
        result = await upload_file_streaming("", UploadFile(filename="a.txt", file=BytesIO(b"new")), overwrite="overwrite")
        assert result["success"]
        assert (tmp_path / "a.txt").read_text() == "new"

    @pytest.mark.asyncio
    async def test_upload_rename_suffixes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import upload_file_streaming
        from fastapi import UploadFile
        from io import BytesIO
        (tmp_path / "a.txt").write_text("old")
        result = await upload_file_streaming("", UploadFile(filename="a.txt", file=BytesIO(b"new")), overwrite="rename")
        assert result["success"]
        assert result["filename"] == "a (copie).txt"
        assert (tmp_path / "a.txt").read_text() == "old"


class TestProperties:
    def test_file_properties(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import get_file_properties
        (tmp_path / "a.txt").write_text("data")
        props = get_file_properties("a.txt")
        assert props["is_dir"] is False
        assert props["size_bytes"] == 4
        assert props["mime"] == "text/plain"
        assert props["path"] == "a.txt"

    def test_dir_properties_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)
        from cloud_panel.storage import get_file_properties
        (tmp_path / "folder").mkdir()
        (tmp_path / "folder" / "a.txt").write_text("x")
        (tmp_path / "folder" / "b.txt").write_text("y")
        props = get_file_properties("folder")
        assert props["is_dir"] is True
        assert props["file_count"] == 2
        assert props["mime"] is None


class TestStoragePerformanceRuntime:
    def test_scandir_cache_expires_using_monotonic_clock(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage as storage
        importlib.reload(storage)

        clock = [100.0]
        calls = 0
        original_scandir = storage.os.scandir

        def counting_scandir(path):
            nonlocal calls
            calls += 1
            return original_scandir(path)

        monkeypatch.setattr(storage.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(storage.os, "scandir", counting_scandir)
        (tmp_path / "file.txt").write_text("data")

        storage.get_cached_scandir(str(tmp_path), ttl=1.0)
        clock[0] = 100.9
        storage.get_cached_scandir(str(tmp_path), ttl=1.0)
        assert calls == 1
        clock[0] = 101.1
        storage.get_cached_scandir(str(tmp_path), ttl=1.0)
        assert calls == 2

    def test_failed_scandir_is_not_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage as storage
        importlib.reload(storage)

        original_scandir = storage.os.scandir
        calls = 0

        def flaky_scandir(path):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("remote unavailable")
            return original_scandir(path)

        (tmp_path / "visible.txt").write_text("ok")
        monkeypatch.setattr(storage.os, "scandir", flaky_scandir)
        with pytest.raises(OSError):
            storage.get_cached_scandir(str(tmp_path))
        result = storage.get_cached_scandir(str(tmp_path))
        assert [item["name"] for item in result] == ["visible.txt"]
        assert calls == 2

    def test_inflight_scandir_is_partitioned_by_cache_generation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import threading
        import cloud_panel.storage as storage
        importlib.reload(storage)

        original_scandir = storage.os.scandir
        first_iteration_created = threading.Event()
        release_first = threading.Event()
        calls = 0
        calls_guard = threading.Lock()

        def gated_scandir(path):
            nonlocal calls
            with calls_guard:
                calls += 1
                call_number = calls
            iterator = original_scandir(path)
            if call_number != 1:
                return iterator

            def delayed_entries():
                with iterator:
                    entries = list(iterator)
                first_iteration_created.set()
                release_first.wait(timeout=2)
                yield from entries

            return delayed_entries()

        (tmp_path / "old.txt").write_text("old")
        monkeypatch.setattr(storage.os, "scandir", gated_scandir)
        first_result = []
        first = threading.Thread(
            target=lambda: first_result.extend(storage.get_cached_scandir(str(tmp_path)))
        )
        first.start()
        assert first_iteration_created.wait(timeout=1)

        (tmp_path / "new.txt").write_text("new")
        storage.clear_scandir_cache()
        second_result = storage.get_cached_scandir(str(tmp_path))
        release_first.set()
        first.join(timeout=2)

        assert [item["name"] for item in first_result] == ["old.txt"]
        assert "new.txt" in {item["name"] for item in second_result}
        assert "new.txt" in {item["name"] for item in storage.get_cached_scandir(str(tmp_path))}

    def test_directory_summary_fingerprints_all_entries_and_reports_true_age(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage as storage
        importlib.reload(storage)
        storage.clear_scandir_cache()

        for index in range(205):
            (tmp_path / f"item-{index:03d}.txt").write_text("x")
        clock = [100.0]
        monkeypatch.setattr(storage.time, "monotonic", lambda: clock[0])
        first = storage.directory_summary("")
        clock[0] = 100.4
        cached = storage.directory_summary("")

        assert first["total"] == 205
        assert len(first["fingerprint"]) == 64
        assert cached["fingerprint"] == first["fingerprint"]
        assert cached["source_age_ms"] == 400
        assert cached["source_max_age_ms"] == 500
        assert first["source_scanned_at"].endswith("Z")

        (tmp_path / "item-204.txt").write_text("changed and larger")
        storage.clear_scandir_cache()
        changed = storage.directory_summary("")
        assert changed["fingerprint"] != first["fingerprint"]


class TestCloudBackgroundJobs:
    def test_background_search_and_live_poll_use_shared_runtime(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from common.performance import JobManager, SharedSectionHub, safe_error
        import importlib
        import time

        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        monkeypatch.setattr("cloud_panel.config.DB_PATH", tmp_path / "test.db")
        import cloud_panel.models
        import cloud_panel.storage
        import cloud_panel.routes.files
        import cloud_panel.routes.jobs
        import cloud_panel.routes.events
        import cloud_panel.main
        importlib.reload(cloud_panel.models)
        importlib.reload(cloud_panel.storage)
        importlib.reload(cloud_panel.routes.files)
        importlib.reload(cloud_panel.routes.jobs)
        importlib.reload(cloud_panel.routes.events)
        importlib.reload(cloud_panel.main)

        app = cloud_panel.main.app
        manager = JobManager(
            max_workers=1,
            max_pending=4,
            queue_size=4,
            state_path=tmp_path / "jobs.json",
            error_factory=lambda _exc: safe_error(),
        )
        app.state.job_manager = manager
        app.state.section_hub = SharedSectionHub(job_manager=manager, default_interval=0.05)
        app.state.action_limiter = type("L", (), {"allow": lambda self, key: True})()
        (tmp_path / "needle.txt").write_text("found")
        (tmp_path / "one").mkdir()
        (tmp_path / "two").mkdir()
        (tmp_path / "three").mkdir()

        with TestClient(app) as client:
            token = client.get("/cloud-panel/api/session").json()["csrfToken"]
            invalid = client.post(
                "/cloud-panel/api/jobs",
                json={
                    "type": "recursive_search",
                    "params": {"q": "needle", "path": "", "offset": "invalid"},
                },
                headers={"X-Cloud-Panel-CSRF": token},
            )
            assert invalid.status_code == 422
            assert invalid.json()["detail"]["code"] == "invalid_job_params"
            response = client.post(
                "/cloud-panel/api/jobs",
                json={
                    "type": "recursive_search",
                    "params": {"q": "needle", "path": "", "offset": 0, "limit": 50},
                },
                headers={"X-Cloud-Panel-CSRF": token},
            )
            assert response.status_code == 202
            job = response.json()["job"]
            assert job["status"] in {"queued", "running", "completed"}

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                current = client.get(f"/cloud-panel/api/jobs/{job['id']}").json()
                if current["status"] == "completed":
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("background search did not complete")

            assert current["result"]["items"][0]["name"] == "needle.txt"
            live = client.get("/cloud-panel/api/live", params={"path": ""})
            assert live.status_code == 200
            assert live.json()["sections"]
            assert any(item["id"] == job["id"] for item in live.json()["jobs"])
            live_frame = next(iter(live.json()["sections"].values()))
            assert live_frame["type"] == "snapshot"
            assert live_frame["data"]["path"] == ""
            assert live_frame["data"]["total"] >= 4
            assert len(live_frame["data"]["fingerprint"]) == 64
            assert isinstance(live_frame["data"]["source_age_ms"], int)
            assert live_frame["data"]["source_max_age_ms"] == 500
            assert live_frame["data"]["source_scanned_at"].endswith("Z")

            first_fingerprint = live_frame["data"]["fingerprint"]
            (tmp_path / "external-change.txt").write_text("visible to polling fallback")
            # Let the metadata cache expire, then poll without opening any SSE
            # stream. The endpoint must collect again instead of returning its
            # retained full frame indefinitely.
            time.sleep(0.55)
            refreshed = client.get("/cloud-panel/api/live", params={"path": ""})
            refreshed_frame = next(iter(refreshed.json()["sections"].values()))
            assert refreshed_frame["data"]["fingerprint"] != first_fingerprint
            assert refreshed_frame["data"]["total"] == live_frame["data"]["total"] + 1

            cloud_panel.routes.events.LIVE_SECTION_MAX_IDLE = 2
            client.get("/cloud-panel/api/live", params={"path": "one/"})
            client.get("/cloud-panel/api/live", params={"path": "./one"})
            assert len(app.state.live_sections) == 2
            client.get("/cloud-panel/api/live", params={"path": "two"})
            client.get("/cloud-panel/api/live", params={"path": "three"})
            assert len(app.state.live_sections) <= 2

    def test_mutation_queues_only_the_changed_remote_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import importlib
        import cloud_panel.storage as storage
        importlib.reload(storage)
        scheduled = []
        monkeypatch.setattr(storage, "schedule_targeted_refresh", scheduled.extend)
        (tmp_path / "parent").mkdir()

        storage.create_directory("parent", "child")

        assert scheduled == ["parent"]

    def test_versioned_assets_only_get_private_long_cache_for_current_hash(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        import importlib
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import cloud_panel.main
        importlib.reload(cloud_panel.main)
        client = TestClient(cloud_panel.main.app)

        current = client.get(f"/cloud-panel/static/dist/app.min.js?v={cloud_panel.main.ASSET_VERSION}")
        stale = client.get("/cloud-panel/static/dist/app.min.js?v=stale")
        manifest = client.get(f"/cloud-panel/static/dist/asset-version.json?v={cloud_panel.main.ASSET_VERSION}")
        index = client.get("/cloud-panel/")
        config = client.get("/cloud-panel/config.js")
        api = client.get("/cloud-panel/api/session")

        assert current.status_code == 200
        assert current.headers["cache-control"] == "private, max-age=31536000, immutable"
        assert stale.headers["cache-control"] == "no-store"
        assert manifest.headers["cache-control"] == "no-store"
        assert index.headers["cache-control"] == "no-store"
        assert config.headers["cache-control"] == "no-store"
        assert api.headers["cache-control"] == "no-store"

        cloud_panel.main.ASSET_VERSION = "invalid"
        invalid = client.get("/cloud-panel/static/dist/app.min.js?v=invalid")
        assert invalid.headers["cache-control"] == "no-store"


class TestDownloadRoutes:
    """The download path must stream large reads with a single stat."""

    def _client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        import importlib
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        import cloud_panel.storage as storage
        importlib.reload(storage)
        import cloud_panel.services.thumbnails as thumbnails
        importlib.reload(thumbnails)
        import cloud_panel.main
        importlib.reload(cloud_panel.main)
        return cloud_panel.main.app, TestClient(cloud_panel.main.app)

    def test_download_streams_full_body_with_metadata(self, tmp_path, monkeypatch):
        _app, client = self._client(tmp_path, monkeypatch)
        payload = b"0123456789abcdef" * (300 * 1024)
        (tmp_path / "big.bin").write_bytes(payload)

        response = client.get("/cloud-panel/api/files/download", params={"path": "big.bin"})

        assert response.status_code == 200
        assert response.content == payload
        assert int(response.headers["content-length"]) == len(payload)
        assert response.headers["accept-ranges"] == "bytes"
        assert "filename=" in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "content-security-policy" in response.headers
        assert response.headers["cache-control"] == "no-store"

    def test_download_head_returns_metadata_only(self, tmp_path, monkeypatch):
        _app, client = self._client(tmp_path, monkeypatch)
        (tmp_path / "small.txt").write_text("hello")

        response = client.head("/cloud-panel/api/files/download", params={"path": "small.txt"})

        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["content-length"] == "5"

    def test_download_supports_byte_ranges(self, tmp_path, monkeypatch):
        _app, client = self._client(tmp_path, monkeypatch)
        (tmp_path / "media.bin").write_bytes(bytes(range(256)) * 4096)

        response = client.get(
            "/cloud-panel/api/files/download",
            params={"path": "media.bin"},
            headers={"Range": "bytes=0-1023"},
        )

        assert response.status_code == 206
        assert len(response.content) == 1024
        assert response.headers["content-range"].startswith("bytes 0-1023/")
        assert response.content == bytes(range(256)) * 4

    def test_download_rejects_traversal(self, tmp_path, monkeypatch):
        _app, client = self._client(tmp_path, monkeypatch)
        (tmp_path / "safe.txt").write_text("safe")

        response = client.get("/cloud-panel/api/files/download", params={"path": "../../etc/passwd"})

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "path_error"

    def test_thumbnail_serves_cached_webp(self, tmp_path, monkeypatch):
        app, client = self._client(tmp_path, monkeypatch)
        cache_dir = tmp_path / "thumbs"
        import cloud_panel.services.thumbnails as thumbnails
        monkeypatch.setattr(thumbnails, "THUMBNAIL_CACHE_DIR", str(cache_dir))
        monkeypatch.setattr(thumbnails, "_created_since_prune", 0)

        from PIL import Image
        source = tmp_path / "photo.png"
        Image.new("RGB", (1600, 1200), (30, 60, 120)).save(source)

        first = client.get("/cloud-panel/api/files/thumbnail", params={"path": "photo.png"})
        assert first.status_code == 200
        assert first.headers["content-type"] == "image/webp"
        assert first.headers["cache-control"] == "private, max-age=3600"
        cached = list(cache_dir.glob("*.webp"))
        assert len(cached) == 1
        with Image.open(cached[0]) as thumbnail:
            assert thumbnail.width <= 320

        second = client.get("/cloud-panel/api/files/thumbnail", params={"path": "photo.png"})
        assert second.status_code == 200
        assert second.content == first.content

    def test_thumbnail_rejects_unsupported_file(self, tmp_path, monkeypatch):
        _app, client = self._client(tmp_path, monkeypatch)
        (tmp_path / "notes.txt").write_text("hello")

        response = client.get("/cloud-panel/api/files/thumbnail", params={"path": "notes.txt"})

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "thumbnail_unavailable"

    def test_thumbnail_invalidates_when_the_source_changes(self, tmp_path, monkeypatch):
        _app, client = self._client(tmp_path, monkeypatch)
        import cloud_panel.services.thumbnails as thumbnails
        monkeypatch.setattr(thumbnails, "THUMBNAIL_CACHE_DIR", str(tmp_path / "thumbs"))
        monkeypatch.setattr(thumbnails, "_created_since_prune", 0)

        from PIL import Image
        source = tmp_path / "photo.png"
        Image.new("RGB", (400, 300), (0, 0, 0)).save(source)
        first = client.get("/cloud-panel/api/files/thumbnail", params={"path": "photo.png"})

        Image.new("RGB", (400, 300), (255, 255, 255)).save(source)
        os.utime(source, (source.stat().st_atime + 10, source.stat().st_mtime + 10))
        second = client.get("/cloud-panel/api/files/thumbnail", params={"path": "photo.png"})

        assert first.content != second.content

    def test_download_uses_dedicated_stream_limiter(self, tmp_path, monkeypatch):
        app, _client = self._client(tmp_path, monkeypatch)
        (tmp_path / "f.txt").write_text("x")

        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            response = client.get("/cloud-panel/api/files/download", params={"path": "f.txt"})

        assert response.status_code == 200
        assert app.state.download_limiter is not None


class TestStreamingFileResponse:
    """The streaming response must never truncate a successful download."""

    @staticmethod
    def _call(response):
        messages = []

        async def send(message):
            messages.append(message)

        asyncio.run(response({"type": "http", "method": "GET", "headers": []}, None, send))
        return messages

    def test_streams_the_whole_file(self, tmp_path):
        from cloud_panel.file_response import StreamingFileResponse

        path = tmp_path / "payload.bin"
        payload = b"z" * (3 * 1024 * 1024 + 7)
        path.write_bytes(payload)
        response = StreamingFileResponse(path, stat_result=os.stat(path))

        messages = self._call(response)

        assert messages[0]["status"] == 200
        body = b"".join(message.get("body", b"") for message in messages[1:])
        assert body == payload
        assert messages[-1]["more_body"] is False

    def test_reports_404_when_the_file_vanishes_after_stat(self, tmp_path):
        from cloud_panel.file_response import StreamingFileResponse

        path = tmp_path / "gone.bin"
        path.write_bytes(b"data")
        response = StreamingFileResponse(path, stat_result=os.stat(path))
        path.unlink()

        messages = self._call(response)

        assert messages[0]["status"] == 404

    def test_single_range_reads_only_the_requested_slice(self, tmp_path):
        from cloud_panel.file_response import StreamingFileResponse

        path = tmp_path / "range.bin"
        path.write_bytes(bytes(range(256)) * 8192)
        response = StreamingFileResponse(path, stat_result=os.stat(path))

        async def send(message):
            messages.append(message)

        messages = []

        async def run():
            await response(
                {
                    "type": "http",
                    "method": "GET",
                    "headers": [(b"range", b"bytes=100-203")],
                },
                None,
                send,
            )

        asyncio.run(run())
        assert messages[0]["status"] == 206
        body = b"".join(message.get("body", b"") for message in messages[1:])
        assert body == (bytes(range(256)) * 8192)[100:204]

