"""Tests backend pour Cloud Panel."""

from __future__ import annotations

import os
import sys
import tempfile
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

    def test_validate_public_id_valid(self):
        assert validate_public_id("abc-123_XYZ") == "abc-123_XYZ"

    def test_validate_public_id_invalid(self):
        with pytest.raises(ValueError, match="invalide"):
            validate_public_id("abc/def")

    def test_validate_public_id_empty(self):
        with pytest.raises(ValueError, match="invalide"):
            validate_public_id("")


class TestFormatSize:
    def test_bytes(self):
        assert format_size(500) == "500.0 B"

    def test_kb(self):
        assert "KB" in format_size(2048)

    def test_mb(self):
        result = format_size(1048576)
        assert "MB" in result or "MiB" in result
        # 1 MB = 1024*1024 = 1048576
        # 1048576 / 1024 = 1024 KB / 1024 = 1 MB
        assert format_size(1048576) == "1.0 MB"

    def test_gb(self):
        result = format_size(1073741824)
        assert "GB" in result


class TestStorage:
    def test_scandir_cache(self, tmp_path):
        clear_scandir_cache()
        # Create a file in tmp_path
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        result = get_cached_scandir(str(tmp_path))
        assert len(result) >= 1
        names = [item["name"] for item in result]
        assert "test.txt" in names

    def test_clear_scandir_cache(self):
        # Just ensure no error
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
        from cloud_panel.config import MOUNT_PATH
        from cloud_panel.storage import list_directory

        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        # Re-import storage to pick up new MOUNT_PATH
        import importlib
        import cloud_panel.storage
        importlib.reload(cloud_panel.storage)

        (tmp_path / "test.txt").write_text("hello")
        result = cloud_panel.storage.list_directory("")
        assert "items" in result
        names = [item["name"] for item in result["items"]]
        assert "test.txt" in names
        assert result["current_path"] == ""

    def test_list_nonexistent(self, tmp_path, monkeypatch):
        from cloud_panel.config import MOUNT_PATH
        import importlib
        import cloud_panel.storage
        monkeypatch.setattr("cloud_panel.config.MOUNT_PATH", str(tmp_path))
        importlib.reload(cloud_panel.storage)

        with pytest.raises(ValueError, match="Chemin hors|Dossier introuvable"):
            cloud_panel.storage.list_directory("nonexistent")
