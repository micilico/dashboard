import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_panel.services.organizer import _missing_torrents, detect_duplicate_groups, duplicate_cloud_operations


def _library(tmp_path: Path) -> Path:
    root = tmp_path / "qbittorrent" / "Films"
    (root / "Dune (2021)").mkdir(parents=True)
    (root / "Dune.2021.1080p").mkdir(parents=True)
    return tmp_path


def test_duplicate_files_are_reused_and_sent_to_trash(tmp_path):
    mount = _library(tmp_path)
    (mount / "qbittorrent/Films/Dune (2021)/Dune.mkv").write_bytes(b"same")
    (mount / "qbittorrent/Films/Dune.2021.1080p/Dune.mkv").write_bytes(b"same")

    groups = detect_duplicate_groups(str(mount), "/srv/qbittorrent")

    assert groups[0]["status"] == "ready"
    assert groups[0]["files"][0]["decision"] == "reuse"
    operations = duplicate_cloud_operations(groups[0], "/srv/qbittorrent")
    assert operations[-1]["op"] == "delete"


def test_complementary_files_can_be_moved(tmp_path):
    mount = _library(tmp_path)
    (mount / "qbittorrent/Films/Dune (2021)/Dune.mkv").write_bytes(b"movie")
    (mount / "qbittorrent/Films/Dune.2021.1080p/Dune.srt").write_bytes(b"subs")

    groups = detect_duplicate_groups(str(mount), "/srv/qbittorrent")

    assert groups[0]["files"][0]["decision"] == "move"
    assert groups[0]["complementaryFiles"] == 1


def test_same_name_with_different_content_is_blocked(tmp_path):
    mount = _library(tmp_path)
    (mount / "qbittorrent/Films/Dune (2021)/Dune.mkv").write_bytes(b"one")
    (mount / "qbittorrent/Films/Dune.2021.1080p/Dune.mkv").write_bytes(b"two")

    groups = detect_duplicate_groups(str(mount), "/srv/qbittorrent")

    assert groups[0]["status"] == "conflict"
    assert groups[0]["files"][0]["decision"] == "conflict"
    assert duplicate_cloud_operations(groups[0], "/srv/qbittorrent") == [
        {"op": "mkdir", "path": "qbittorrent/Films", "name": "Dune (2021)"}
    ]


def test_missing_check_accepts_unicode_normalization_and_unknown_qbit_size(tmp_path):
    media = tmp_path / "qbittorrent" / "Films"
    media.mkdir(parents=True)
    cloud_name = "Quand Harry rencontré Sally (1989) (When Harry Met Sally...).mkv"
    (media / cloud_name).write_bytes(b"movie")
    torrent_name = "Quand Harry rencontre\u0301 Sally (1989) (When Harry Met Sally...).mkv"

    missing = _missing_torrents(
        str(tmp_path),
        "/srv/qbittorrent",
        [{"hash": "a" * 40, "name": "Quand Harry rencontre Sally", "state": "pausedDL"}],
        [[{"name": torrent_name, "size": 0}]],
    )

    assert missing == []
