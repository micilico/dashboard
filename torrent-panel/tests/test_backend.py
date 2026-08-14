import asyncio
import os
import tempfile
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_panel.main import (  # noqa: E402
    MediaAutomationConfig,
    MediaAutomationError,
    MediaAutomationManager,
    RateLimiter,
    app,
    cleanup_csrf_tokens,
    error_detail,
    validate_hash,
    validate_magnet,
)
from torrent_panel.qbittorrent import QBittorrentClient, QbitConfig, QbitError  # noqa: E402
from torrent_panel.routes import dashboard as dashboard_routes  # noqa: E402
from torrent_panel.routes import torrents as torrent_routes  # noqa: E402
from torrent_panel.services.monitoring import (
    _disk_snapshot,
    _disk_unavailable,
    _fetch_ultra_quota,
    _parse_ultra_quota,
    storage_snapshot,
)  # noqa: E402
from torrent_panel.services import relink as relink_service  # noqa: E402
from torrent_panel import config as panel_config  # noqa: E402
from torrent_panel.services.ratio_monitor import MAX_THRESHOLD, MIN_THRESHOLD, RatioMonitor, RatioThresholdError  # noqa: E402
from torrent_panel.services.stats import StatsStore  # noqa: E402
from torrent_panel.services.tracker_stats import TrackerStatsStore  # noqa: E402


VALID_HASH = "a" * 40


class FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload
        self.status_code = 200
        self.text = "Ok."

    def json(self):
        return self._payload


class FakeQbit:
    def __init__(self):
        self.calls = []
        self.torrents_payload = []
        self.trackers_payload = []
        self.categories_payload = {}
        self.files_payload = {}

    async def torrents(self):
        return list(self.torrents_payload)

    async def categories(self):
        return dict(self.categories_payload)

    async def files(self, torrent_hash):
        self.calls.append(("files", torrent_hash))
        return list(self.files_payload.get(torrent_hash, []))

    async def pause_many(self, hashes):
        self.calls.append(("pause", list(hashes)))

    async def resume_many(self, hashes):
        self.calls.append(("resume", hashes))

    async def set_force_start_many(self, hashes, enabled):
        self.calls.append(("force_start", hashes, enabled))

    async def delete_many(self, hashes, delete_files):
        self.calls.append(("delete", hashes, delete_files))

    async def add_magnet(self, magnet, **kwargs):
        self.calls.append(("add", magnet, kwargs))

    async def trackers(self, torrent_hash):
        self.calls.append(("trackers", torrent_hash))
        return list(self.trackers_payload)

    async def add_tracker(self, torrent_hash, tracker_url):
        self.calls.append(("add_tracker", torrent_hash, tracker_url))

    async def set_location_many(self, hashes, location):
        self.calls.append(("set_location", list(hashes), location))

    async def set_content_layout_many(self, hashes, layout):
        self.calls.append(("set_content_layout", list(hashes), layout))

    async def recheck_many(self, hashes):
        self.calls.append(("recheck", list(hashes)))

    async def ready(self):
        return True

    async def close(self):
        return None


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.original_qbit = app.state.qbit
        self.original_media = app.state.media_automation
        self.original_tracker_stats = app.state.tracker_stats
        self.original_stats = app.state.stats
        self.original_ratio_monitor = app.state.ratio_monitor
        self.original_limiter = app.state.action_limiter
        self.original_csrf_tokens = dict(app.state.csrf_tokens)
        self.original_tr4ker_announce_url = torrent_routes.TR4KER_ANNOUNCE_URL
        app.state.qbit = FakeQbit()
        temp_state = Path(tempfile.mkdtemp()) / "media.json"
        app.state.media_automation = MediaAutomationManager(
            app.state.qbit,
            MediaAutomationConfig(
                enabled=False,
                poll_seconds=8,
                debounce_seconds=5,
                jellyfin_delay_seconds=0,
                max_rclone_retries=1,
                max_mount_retries=1,
                max_jellyfin_retries=1,
                history_limit=10,
                state_path=temp_state,
                mount_path="/tmp",
                rclone_refresh_mode="rc",
                rclone_rc_refresh_url="http://127.0.0.1:5572/vfs/refresh",
                rclone_rc_refresh_dir="",
                rclone_systemd_unit="",
                rclone_systemd_restart_cmd="",
                jellyfin_api_url="http://127.0.0.1:8096",
                jellyfin_api_key="token",
                jellyfin_library_map={},
                jellyfin_global_fallback=True,
            ),
        )
        app.state.tracker_stats = TrackerStatsStore(temp_state.parent / "tracker-stats.json")
        app.state.stats = StatsStore(temp_state.parent / "stats.json", history_days=30)
        app.state.ratio_monitor = RatioMonitor(temp_state.parent / "ratio-monitor.json", threshold=10.0)
        app.state.action_limiter = RateLimiter(max_calls=100, period_seconds=60, max_keys=100)
        app.state.csrf_tokens = {}
        self.client = TestClient(app)
        session = self.client.get("/torrent-panel/api/session").json()
        self.csrf = session["csrfToken"]

    def tearDown(self):
        self.client.close()
        app.state.qbit = self.original_qbit
        app.state.media_automation = self.original_media
        app.state.tracker_stats = self.original_tracker_stats
        app.state.stats = self.original_stats
        app.state.ratio_monitor = self.original_ratio_monitor
        app.state.action_limiter = self.original_limiter
        app.state.csrf_tokens = self.original_csrf_tokens
        torrent_routes.TR4KER_ANNOUNCE_URL = self.original_tr4ker_announce_url

    def post_action(self, path, payload):
        return self.client.post(
            path,
            json=payload,
            headers={"X-Torrent-Panel-CSRF": self.csrf},
        )

    def test_hash_validation_rejects_bad_hash(self):
        self.assertEqual(validate_hash(VALID_HASH.upper()), VALID_HASH)
        with self.assertRaises(Exception):
            validate_hash("not-a-hash")

    def test_magnet_validation_reports_each_reason(self):
        valid, reason = validate_magnet("magnet:?xt=urn:btih:" + VALID_HASH)
        self.assertIsNotNone(valid)
        self.assertIsNone(reason)
        valid, reason = validate_magnet("https://example.test/file")
        self.assertIsNone(valid)
        self.assertEqual(reason, "Lien magnet invalide.")

    def test_csrf_error_is_structured(self):
        response = self.client.post("/torrent-panel/api/torrents/pause", json={"hashes": [VALID_HASH]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "csrf_expired")

    def test_new_session_does_not_invalidate_previous_tab_token(self):
        first_token = self.csrf
        second_token = self.client.get("/torrent-panel/api/session").json()["csrfToken"]
        self.assertNotEqual(first_token, second_token)

        response = self.client.post(
            "/torrent-panel/api/torrents/pause",
            json={"hashes": [VALID_HASH]},
            headers={"X-Torrent-Panel-CSRF": first_token, "Cookie": f"torrent_panel_csrf={first_token}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(app.state.qbit.calls[-1], ("pause", [VALID_HASH]))

    def test_expired_csrf_token_is_rejected(self):
        token = self.csrf
        app.state.csrf_tokens[token] = -1_000_000
        cleanup_csrf_tokens(app, now=1_000_000)

        response = self.client.post(
            "/torrent-panel/api/torrents/pause",
            json={"hashes": [VALID_HASH]},
            headers={"X-Torrent-Panel-CSRF": token, "Cookie": f"torrent_panel_csrf={token}"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "csrf_expired")

    def test_action_accepts_matching_token_among_duplicate_path_cookies(self):
        response = self.client.post(
            "/torrent-panel/api/torrents/pause",
            json={"hashes": [VALID_HASH]},
            headers={
                "X-Torrent-Panel-CSRF": self.csrf,
                "Cookie": f"torrent_panel_csrf={self.csrf}; torrent_panel_csrf=stale-token",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(app.state.qbit.calls[-1], ("pause", [VALID_HASH]))

    def test_session_response_is_not_cacheable(self):
        response = self.client.get("/torrent-panel/api/session")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("httponly", response.headers["set-cookie"].lower())

    def test_tracker_details_hide_private_url_components(self):
        app.state.qbit.trackers_payload = [
            {
                "url": "https://user:password@tracker.test/private/announce?passkey=secret",
                "status": 2,
                "msg": "failed https://tracker.test/announce?passkey=secret",
            }
        ]

        response = self.client.get(f"/torrent-panel/api/torrents/{VALID_HASH}/trackers")

        self.assertEqual(response.status_code, 200)
        tracker = response.json()["trackers"][0]
        self.assertEqual(tracker["url"], "https://tracker.test/private/announce")
        self.assertNotIn("secret", str(tracker))
        self.assertNotIn("password", str(tracker))

    def test_tracker_index_returns_structured_error_when_qbit_slow(self):
        cache = torrent_routes._TRACKER_INDEX_CACHE
        original = dict(cache)
        cache["data"] = None
        cache["ts"] = 0.0

        async def failing_torrents():
            raise QbitError(504, "qBittorrent ne répond pas assez vite.", code="qbit_timeout", recovery="Réessayer")

        app.state.qbit.torrents = failing_torrents
        try:
            response = self.client.get("/torrent-panel/api/trackers/index")
        finally:
            cache.update(original)

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["detail"]["code"], "qbit_timeout")

    def test_tracker_index_serves_stale_cache_when_qbit_slow(self):
        cache = torrent_routes._TRACKER_INDEX_CACHE
        original = dict(cache)
        cache["data"] = {"index": {"hash": ["tracker.test"]}, "domains": {"tracker.test": 1}}
        cache["ts"] = 0.0

        async def failing_torrents():
            raise QbitError(504, "qBittorrent ne répond pas assez vite.", code="qbit_timeout", recovery="Réessayer")

        app.state.qbit.torrents = failing_torrents
        try:
            response = self.client.get("/torrent-panel/api/trackers/index")
        finally:
            cache.update(original)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["index"], {"hash": ["tracker.test"]})

    def test_configured_tr4ker_tracker_adds_private_announce_url(self):
        tracker_url = "https://tr4ker.test/announce?passkey=secret"
        torrent_routes.TR4KER_ANNOUNCE_URL = tracker_url
        app.state.qbit.torrents_payload = [{"hash": VALID_HASH, "name": "Private", "isPrivate": True}]

        response = self.post_action("/torrent-panel/api/torrents/add-tr4ker-tracker", {"hashes": [VALID_HASH]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)
        self.assertIn(("add_tracker", VALID_HASH, tracker_url), app.state.qbit.calls)

    def test_configured_tr4ker_tracker_reports_missing_env(self):
        torrent_routes.TR4KER_ANNOUNCE_URL = ""
        app.state.qbit.torrents_payload = [{"hash": VALID_HASH, "name": "Private", "isPrivate": True}]

        response = self.post_action("/torrent-panel/api/torrents/add-tr4ker-tracker", {"hashes": [VALID_HASH]})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "tr4ker_tracker_not_configured")

    def test_group_actions_send_hashes_once(self):
        second_hash = "b" * 40
        response = self.post_action("/torrent-panel/api/torrents/pause", {"hashes": [VALID_HASH, second_hash]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(app.state.qbit.calls[-1], ("pause", [VALID_HASH, second_hash]))

    def test_delete_accepts_legacy_single_hash_payload(self):
        response = self.post_action("/torrent-panel/api/torrents/delete", {"hash": VALID_HASH, "deleteFiles": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app.state.qbit.calls[-1], ("delete", [VALID_HASH], True))

    def test_force_start_updates_selected_torrents(self):
        second_hash = "b" * 40
        response = self.post_action(
            "/torrent-panel/api/torrents/force-start",
            {"hashes": [VALID_HASH, second_hash], "enabled": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["enabled"], True)
        self.assertEqual(app.state.qbit.calls[-1], ("force_start", [VALID_HASH, second_hash], True))

    def test_add_multiple_magnets_keeps_rejections(self):
        response = self.post_action(
            "/torrent-panel/api/torrents/add",
            {
                "magnets": [
                    "magnet:?xt=urn:btih:" + VALID_HASH,
                    "not-a-magnet",
                ],
                "category": "Films",
                "tags": "archive",
                "paused": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["accepted"], 1)
        self.assertEqual(len(body["rejected"]), 1)
        self.assertEqual(app.state.qbit.calls[-1][0], "add")
        self.assertEqual(app.state.qbit.calls[-1][2]["category"], "Films")

    def test_add_tr4ker_magnet_injects_private_tracker_backend_side(self):
        tracker_url = "https://tr4ker.test/announce?passkey=secret"
        torrent_routes.TR4KER_ANNOUNCE_URL = tracker_url

        response = self.post_action(
            "/torrent-panel/api/torrents/add",
            {
                "magnets": ["magnet:?xt=urn:btih:" + VALID_HASH],
                "addTr4kerTracker": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["tr4kerTrackerApplied"])
        sent_magnet = app.state.qbit.calls[-1][1]
        self.assertIn("tr=https%3A%2F%2Ftr4ker.test%2Fannounce%3Fpasskey%3Dsecret", sent_magnet)

    def test_add_tr4ker_magnet_reports_missing_env(self):
        torrent_routes.TR4KER_ANNOUNCE_URL = ""

        response = self.post_action(
            "/torrent-panel/api/torrents/add",
            {
                "magnets": ["magnet:?xt=urn:btih:" + VALID_HASH],
                "addTr4kerTracker": True,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "tr4ker_tracker_not_configured")

    def test_rate_limiter_is_bounded(self):
        limiter = RateLimiter(max_calls=1, period_seconds=60, max_keys=2)
        self.assertTrue(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))
        self.assertTrue(limiter.allow("c"))
        self.assertLessEqual(len(limiter._hits), 2)

    def test_manual_rclone_action_endpoint(self):
        calls = []

        async def fake_manual_action(action):
            calls.append(action)
            return {"status": "ok", "message": "Actualisation rclone lancée."}

        app.state.media_automation.manual_action = fake_manual_action
        response = self.post_action("/torrent-panel/api/media-actions/rclone-refresh", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(calls, ["rclone-refresh"])

    def test_manual_jellyfin_action_endpoint(self):
        calls = []

        async def fake_manual_action(action):
            calls.append(action)
            return {"status": "ok", "message": "Scan Jellyfin lancé."}

        app.state.media_automation.manual_action = fake_manual_action
        response = self.post_action("/torrent-panel/api/media-actions/jellyfin-refresh", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(calls, ["jellyfin-refresh"])

    def test_dashboard_exposes_overview_blocks(self):
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Ubuntu",
                "state": "downloading",
                "downloadSpeed": 4096,
                "uploadSpeed": 1024,
            }
        ]

        response = self.client.get("/torrent-panel/api/dashboard")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("overview", body)
        self.assertIn("recentActivity", body)
        self.assertIn("storage", body)
        self.assertEqual(body["overview"]["activeTorrents"], 1)
        self.assertGreaterEqual(body["overview"]["downloadSpeedBytes"], 4096)

    def test_tracker_stats_records_daily_deltas_by_tracker(self):
        app.state.qbit.trackers_payload = [{"url": "https://tracker.test/announce"}]
        app.state.qbit.torrents_payload = [
            {"hash": VALID_HASH, "name": "Ubuntu", "downloaded": 1000, "uploaded": 100, "tracker": "tracker.test"}
        ]
        first = self.client.get("/torrent-panel/api/torrents/stats/trackers")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["stats"]["totals"][0]["downloaded"], 0)

        app.state.qbit.torrents_payload = [
            {"hash": VALID_HASH, "name": "Ubuntu", "downloaded": 1500, "uploaded": 350, "tracker": "tracker.test"}
        ]
        second = self.client.get("/torrent-panel/api/torrents/stats/trackers")

        self.assertEqual(second.status_code, 200)
        stats = second.json()["stats"]
        self.assertEqual(stats["totals"][0]["tracker"], "tracker.test")
        self.assertEqual(stats["totals"][0]["downloaded"], 500)
        self.assertEqual(stats["totals"][0]["uploaded"], 250)
        self.assertEqual(stats["totals"][0]["ratio"], 0.5)

    def test_stats_store_records_daily_deltas_and_totals(self):
        first = app.state.stats.observe(
            [
                {
                    "hash": VALID_HASH,
                    "name": "Ubuntu",
                    "downloaded": 1000,
                    "uploaded": 100,
                    "state": "downloading",
                    "downloadSpeed": 1024,
                    "uploadSpeed": 512,
                }
            ],
            disk={"usedPercent": 42.5, "freeBytes": 1000, "totalBytes": 2000},
        )
        self.assertEqual(first["totals"]["observedDays"], 1)
        self.assertEqual(first["daily"][-1]["downloaded"], 0)

        second = app.state.stats.observe(
            [
                {
                    "hash": VALID_HASH,
                    "name": "Ubuntu",
                    "downloaded": 1500,
                    "uploaded": 350,
                    "state": "uploading",
                    "downloadSpeed": 0,
                    "uploadSpeed": 64,
                }
            ],
            disk={"usedPercent": 50.0, "freeBytes": 1000, "totalBytes": 2000},
        )
        last = second["daily"][-1]
        self.assertEqual(last["downloaded"], 500)
        self.assertEqual(last["uploaded"], 250)
        self.assertEqual(last["ratio"], 0.5)
        self.assertEqual(second["totals"]["downloaded"], 500)
        self.assertEqual(second["totals"]["uploaded"], 250)
        self.assertEqual(second["totals"]["ratio"], 0.5)
        self.assertEqual(last["diskUsedPercent"], 50.0)
        self.assertEqual(last["downloadingTorrents"], 0)
        self.assertEqual(last["activeTorrents"], 1)

    def test_stats_store_trims_days_window(self):
        store = StatsStore(app.state.stats._state_path, history_days=7)
        days = {}
        for index in range(10):
            days[f"2026-08-{index + 1:02d}"] = {"downloaded": 100 * (index + 1), "uploaded": 10 * (index + 1), "ratio": 0.1}
        store._save({"updatedAt": "2026-08-10T00:00:00+00:00", "torrents": {}, "days": days})
        snapshot = store.snapshot()
        self.assertEqual(len(snapshot["daily"]), 7)
        self.assertEqual(snapshot["daily"][0]["date"], "2026-08-04")
        self.assertEqual(snapshot["totals"]["downloaded"], sum(100 * (index + 1) for index in range(10)))

    def test_ratio_monitor_flags_high_ratio(self):
        monitor = app.state.ratio_monitor
        findings = monitor.evaluate(
            [
                {"hash": VALID_HASH, "name": "Seed", "uploaded": 11000, "downloaded": 1000, "tracker": "tracker.test"},
                {"hash": "b" * 40, "name": "OK", "uploaded": 5000, "downloaded": 1000},
                {"hash": "c" * 40, "name": "Pas de DL", "uploaded": 5000, "downloaded": 0},
            ]
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["hash"], VALID_HASH)
        self.assertEqual(findings[0]["ratio"], 11.0)

    def test_ratio_monitor_build_alerts_uses_stable_code(self):
        alerts = app.state.ratio_monitor.build_alerts(
            [{"hash": VALID_HASH, "name": "Seed", "uploaded": 11000, "downloaded": 1000}]
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["code"], f"ratio_high_{VALID_HASH}")
        self.assertEqual(alerts[0]["severity"], "warning")
        self.assertIn("11.0", alerts[0]["message"])

    def test_ratio_monitor_threshold_persists_across_instances(self):
        app.state.ratio_monitor.set_threshold(15.0)
        reloaded = RatioMonitor(app.state.ratio_monitor._state_path, threshold=10.0)
        self.assertEqual(reloaded.threshold, 15.0)

    def test_ratio_monitor_snaps_to_step_and_rejects_out_of_bounds(self):
        monitor = app.state.ratio_monitor
        monitor.set_threshold(15.3)
        self.assertEqual(monitor.threshold, 15.5)
        with self.assertRaises(RatioThresholdError):
            monitor.set_threshold(0.5)
        with self.assertRaises(RatioThresholdError):
            monitor.set_threshold(MAX_THRESHOLD + 1)
        self.assertEqual(MIN_THRESHOLD, 1.0)

    def test_ratio_threshold_endpoint_updates_threshold(self):
        response = self.post_action("/torrent-panel/api/stats/ratio-threshold", {"threshold": 15})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settings"]["threshold"], 15.0)
        self.assertEqual(app.state.ratio_monitor.threshold, 15.0)

    def test_ratio_threshold_endpoint_rejects_out_of_bounds(self):
        response = self.post_action("/torrent-panel/api/stats/ratio-threshold", {"threshold": 200})
        self.assertEqual(response.status_code, 422)
        response = self.post_action("/torrent-panel/api/stats/ratio-threshold", {"threshold": 0})
        self.assertEqual(response.status_code, 422)

    def test_stats_endpoint_returns_persistent_payload(self):
        async def fake_stats_snapshot(app):
            return {
                "generatedAt": "2026-08-06T00:00:00+00:00",
                "stats": app.state.stats.observe(
                    [{"hash": VALID_HASH, "name": "X", "downloaded": 100, "uploaded": 10}]
                ),
                "ratioThreshold": app.state.ratio_monitor.settings(),
                "ratioAlerts": app.state.ratio_monitor.evaluate(
                    [{"hash": VALID_HASH, "name": "X", "downloaded": 100, "uploaded": 10000}]
                ),
            }

        with mock.patch.object(dashboard_routes, "stats_snapshot", new=fake_stats_snapshot):
            response = self.client.get("/torrent-panel/api/stats")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("stats", body)
        self.assertEqual(body["ratioThreshold"]["threshold"], 10.0)
        self.assertEqual(len(body["ratioAlerts"]), 1)
        self.assertEqual(body["ratioAlerts"][0]["ratio"], 100.0)


class RelinkTests(BackendTests):
    def setUp(self):
        super().setUp()
        self.mount = Path(tempfile.mkdtemp())
        film_dir = self.mount / "Qbittorrent" / "Films" / "Dune (2021)" / "Dune.2021.1080p"
        film_dir.mkdir(parents=True)
        (film_dir / "Dune.mkv").write_bytes(b"keep")
        series_dir = self.mount / "Qbittorrent" / "Series" / "Show" / "Saison 1" / "Show.S01"
        series_dir.mkdir(parents=True)
        (series_dir / "Show.S01E01.mkv").write_bytes(b"episode")
        self._original_mount = relink_service.MEDIA_MOUNT_PATH
        relink_service.MEDIA_MOUNT_PATH = str(self.mount)
        relink_service._SCAN_CACHE["data"] = None
        relink_service._SCAN_CACHE["ts"] = 0.0

    def tearDown(self):
        relink_service.MEDIA_MOUNT_PATH = self._original_mount
        relink_service._SCAN_CACHE["data"] = None
        relink_service._SCAN_CACHE["ts"] = 0.0
        super().tearDown()

    def build_payload(self):
        app.state.qbit.categories_payload = {
            "Films": {"savePath": "/mnt/ultra-media/Qbittorrent/Films", "name": "Films"},
            "Series": {"savePath": "/mnt/ultra-media/Qbittorrent/Series", "name": "Series"},
        }
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Dune.2021.1080p",
                "state": "pausedDL",
                "category": "Films",
                "savePath": "/mnt/ultra-media/Qbittorrent/Films",
                "contentPath": "/mnt/ultra-media/Qbittorrent/Dune.2021.1080p",
            },
            {
                "hash": "b" * 40,
                "name": "Show.S01",
                "state": "missingFiles",
                "category": "Series",
                "savePath": "/mnt/ultra-media/Qbittorrent/Series",
                "contentPath": "/mnt/ultra-media/Qbittorrent/Show.S01",
            },
            {
                "hash": "c" * 40,
                "name": "Déjà aligné",
                "state": "uploading",
                "category": "Films",
                "savePath": "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)/Dune.2021.1080p",
            },
            {"hash": "d" * 40, "name": "Sans catégorie", "state": "missingFiles", "category": "", "savePath": "/old/downloads/Other"},
        ]
        app.state.qbit.files_payload = {
            VALID_HASH: [{"name": "Dune.mkv", "size": 4}],
            "b" * 40: [{"name": "Show.S01E01.mkv", "size": 7}],
        }

    def test_preview_locates_organized_folders(self):
        self.build_payload()
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["relinkCount"], 2)
        self.assertEqual(plan["skippedCount"], 1)
        self.assertEqual(plan["layout"], "NoSubfolder")
        locations = sorted(group["location"] for group in plan["relink"])
        self.assertEqual(
            locations,
            [
                "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)/Dune.2021.1080p",
                "/mnt/ultra-media/Qbittorrent/Series/Show/Saison 1/Show.S01",
            ],
        )
        self.assertEqual(plan["skipped"][0]["reason"], "Sans catégorie ou chemin configuré")

    def test_apply_pauses_then_relinks_with_no_subfolder(self):
        self.build_payload()
        response = self.post_action("/torrent-panel/api/torrents/relink", {})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["relinked"], 2)
        self.assertEqual(body["result"]["paused"], 2)
        self.assertEqual(body["result"]["rechecked"], 2)
        calls = app.state.qbit.calls
        self.assertIn(("pause", [VALID_HASH, "b" * 40]), calls)
        self.assertLess(calls.index(("pause", [VALID_HASH, "b" * 40])), calls.index(("set_location", [VALID_HASH], "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)/Dune.2021.1080p")))
        self.assertIn(("set_location", [VALID_HASH], "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)/Dune.2021.1080p"), calls)
        self.assertIn(("set_location", ["b" * 40], "/mnt/ultra-media/Qbittorrent/Series/Show/Saison 1/Show.S01"), calls)
        self.assertIn(("set_content_layout", [VALID_HASH], "NoSubfolder"), calls)
        self.assertIn(("set_content_layout", ["b" * 40], "NoSubfolder"), calls)
        self.assertEqual(calls[-1], ("recheck", [VALID_HASH, "b" * 40]))

    def test_preview_mode_never_mutates(self):
        self.build_payload()
        response = self.post_action("/torrent-panel/api/torrents/relink", {"preview": True})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["result"])
        mutating = [call for call in app.state.qbit.calls if call[0] in {"pause", "set_location", "set_content_layout", "recheck"}]
        self.assertEqual(mutating, [])

    def test_relink_requires_csrf(self):
        self.build_payload()
        response = self.client.post("/torrent-panel/api/torrents/relink", json={})
        self.assertEqual(response.status_code, 403)

    def test_relink_filtered_by_selection(self):
        self.build_payload()
        response = self.post_action("/torrent-panel/api/torrents/relink", {"hashes": [VALID_HASH]})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["plan"]["relinkCount"], 1)
        self.assertEqual(body["result"]["relinked"], 1)
        self.assertIn(("pause", [VALID_HASH]), app.state.qbit.calls)
        self.assertIn(("set_location", [VALID_HASH], "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)/Dune.2021.1080p"), app.state.qbit.calls)

    def test_locates_files_directly_when_no_preserved_folder(self):
        app.state.qbit.categories_payload = {
            "Films": {"savePath": "/mnt/ultra-media/Qbittorrent/Films", "name": "Films"},
        }
        nested_dir = self.mount / "Qbittorrent" / "Films" / "Dune (2021)" / "Dune.2021.1080p"
        if nested_dir.exists():
            import shutil

            shutil.rmtree(nested_dir)
        loose_dir = self.mount / "Qbittorrent" / "Films" / "Dune (2021)"
        loose_dir.mkdir(parents=True, exist_ok=True)
        (loose_dir / "Dune.mkv").write_bytes(b"keep")
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Dune.2021.1080p",
                "state": "pausedDL",
                "category": "Films",
                "savePath": "/mnt/ultra-media/Qbittorrent/Films",
                "contentPath": "/mnt/ultra-media/Qbittorrent/autre-nom",
            },
        ]
        app.state.qbit.files_payload = {VALID_HASH: [{"name": "Dune.mkv", "size": 4}]}
        relink_service._SCAN_CACHE["data"] = None
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["relinkCount"], 1)
        self.assertEqual(plan["relink"][0]["location"], "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)")

    def test_no_relink_when_nothing_affected(self):
        app.state.qbit.categories_payload = {
            "Films": {"savePath": "/mnt/ultra-media/Qbittorrent/Films", "name": "Films"},
        }
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Sain",
                "state": "uploading",
                "category": "Films",
                "savePath": "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)/Dune.2021.1080p",
            },
        ]
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["total"], 0)
        self.assertEqual(plan["relinkCount"], 0)

    def test_relink_status_counts_missing_and_relinked_roots(self):
        app.state.qbit.categories_payload = {
            "Films": {"savePath": "/mnt/ultra-media/Qbittorrent/Films", "name": "Films"},
        }
        app.state.qbit.torrents_payload = [
            {"hash": VALID_HASH, "name": "Manquant", "state": "missingFiles", "category": "Films", "savePath": "/old/thing"},
            {"hash": "b" * 40, "name": "Repos à la racine", "state": "pausedDL", "category": "Films", "savePath": "/mnt/ultra-media/Qbittorrent/Films"},
            {"hash": "c" * 40, "name": "Sain", "state": "uploading", "category": "Films", "savePath": "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)"},
            {"hash": "e" * 40, "name": "En pause hors racine", "state": "stoppedDL", "category": "Films", "savePath": "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)/Dune.2021.1080p"},
        ]
        response = self.client.get("/torrent-panel/api/torrents/relink-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 3)

    def test_relink_status_counts_torrents_at_qbit_root_without_category_savepath(self):
        relink_service.QBIT_SAVE_PATH = "/home/micilico/downloads/qbittorrent"
        panel_config.QBIT_SAVE_PATH = "/home/micilico/downloads/qbittorrent"
        try:
            app.state.qbit.categories_payload = {
                "Films": {"savePath": "", "name": "Films"},
            }
            app.state.qbit.torrents_payload = [
                {"hash": VALID_HASH, "name": "À la racine", "state": "uploading", "category": "Films", "savePath": "/home/micilico/downloads/qbittorrent"},
                {"hash": "b" * 40, "name": "Dans Films", "state": "uploading", "category": "Films", "savePath": "/home/micilico/downloads/qbittorrent/Films/Dune (2021)"},
            ]
            response = self.client.get("/torrent-panel/api/torrents/relink-status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["count"], 1)
        finally:
            relink_service.QBIT_SAVE_PATH = ""
            panel_config.QBIT_SAVE_PATH = ""

    def test_relink_plan_anchors_via_qbit_root_subfolder(self):
        """Scénario réel : mount contient qbittorrent/Films, torrent à la racine qBittorrent, catégorie sans savePath."""
        relink_service.QBIT_SAVE_PATH = "/home/micilico/downloads/qbittorrent"
        panel_config.QBIT_SAVE_PATH = "/home/micilico/downloads/qbittorrent"
        try:
            import shutil

            qbit_setup_dir = self.mount / "Qbittorrent"
            if qbit_setup_dir.exists():
                shutil.rmtree(qbit_setup_dir)
            film_dir = self.mount / "qbittorrent" / "Films" / "Dune (2021)"
            film_dir.mkdir(parents=True, exist_ok=True)
            (film_dir / "Dune.mkv").write_bytes(b"keep")
            app.state.qbit.categories_payload = {
                "Films": {"savePath": "", "name": "Films"},
            }
            app.state.qbit.torrents_payload = [
                {
                    "hash": VALID_HASH,
                    "name": "Dune.2021.1080p",
                    "state": "pausedDL",
                    "category": "Films",
                    "savePath": "/home/micilico/downloads/qbittorrent",
                    "contentPath": "/home/micilico/downloads/qbittorrent/Dune.2021.1080p",
                },
            ]
            app.state.qbit.files_payload = {VALID_HASH: [{"name": "Dune.mkv", "size": 4}]}
            relink_service._SCAN_CACHE["data"] = None
            response = self.client.get("/torrent-panel/api/torrents/relink-preview")
            self.assertEqual(response.status_code, 200)
            plan = response.json()["plan"]
            self.assertEqual(plan["relinkCount"], 1)
            self.assertEqual(plan["relink"][0]["location"], "/home/micilico/downloads/qbittorrent/Films/Dune (2021)")
        finally:
            relink_service.QBIT_SAVE_PATH = ""
            panel_config.QBIT_SAVE_PATH = ""

    def test_relink_locates_renamed_files_in_different_category(self):
        """Cas réel : fichiers renommés par le rangement + catégorie prowlarr mais contenu rangé dans Films."""
        relink_service.QBIT_SAVE_PATH = "/home/micilico/downloads/qbittorrent"
        panel_config.QBIT_SAVE_PATH = "/home/micilico/downloads/qbittorrent"
        try:
            import shutil

            qbit_setup_dir = self.mount / "Qbittorrent"
            if qbit_setup_dir.exists():
                shutil.rmtree(qbit_setup_dir)
            film_dir = self.mount / "qbittorrent" / "Films" / "Backrooms (2026)"
            film_dir.mkdir(parents=True, exist_ok=True)
            (film_dir / "Backrooms.2026.MULTi.CA.2160p.WEB.H265-SUPPLY.mkv").write_bytes(b"0123456789")
            (film_dir / "Backrooms.2026.MULTi.CA.2160p.WEB.H265-SUPPLY.nfo").write_bytes(b"nfo")
            app.state.qbit.categories_payload = {
                "prowlarr": {"savePath": "", "name": "prowlarr"},
                "Films": {"savePath": "", "name": "Films"},
            }
            app.state.qbit.torrents_payload = [
                {
                    "hash": VALID_HASH,
                    "name": "Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY",
                    "state": "pausedDL",
                    "category": "prowlarr",
                    "savePath": "/home/micilico/downloads/qbittorrent",
                    "contentPath": "/home/micilico/downloads/qbittorrent/Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY",
                },
            ]
            app.state.qbit.files_payload = {
                VALID_HASH: [
                    {"name": "Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY.mkv", "size": 10},
                    {"name": "Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY.nfo", "size": 3},
                ]
            }
            relink_service._SCAN_CACHE["data"] = None
            response = self.client.get("/torrent-panel/api/torrents/relink-preview")
            self.assertEqual(response.status_code, 200)
            plan = response.json()["plan"]
            self.assertEqual(plan["relinkCount"], 1)
            self.assertEqual(plan["relink"][0]["location"], "/home/micilico/downloads/qbittorrent/Films/Backrooms (2026)")
        finally:
            relink_service.QBIT_SAVE_PATH = ""
            panel_config.QBIT_SAVE_PATH = ""

    def test_relink_plan_includes_paused_downloads(self):
        app.state.qbit.categories_payload = {
            "Films": {"savePath": "/mnt/ultra-media/Qbittorrent/Films", "name": "Films"},
        }
        nested_dir = self.mount / "Qbittorrent" / "Films" / "Dune (2021)" / "Dune.2021.1080p"
        if nested_dir.exists():
            import shutil

            shutil.rmtree(nested_dir)
        loose_dir = self.mount / "Qbittorrent" / "Films" / "Dune (2021)"
        loose_dir.mkdir(parents=True, exist_ok=True)
        (loose_dir / "Dune.mkv").write_bytes(b"keep")
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Dune.2021.1080p",
                "state": "stoppedDL",
                "category": "Films",
                "savePath": "/mnt/ultra-media/Qbittorrent/Films",
                "contentPath": "/mnt/ultra-media/Qbittorrent/Films/Dune.2021.1080p",
            },
        ]
        app.state.qbit.files_payload = {VALID_HASH: [{"name": "Dune.mkv", "size": 4}]}
        relink_service._SCAN_CACHE["data"] = None
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["relinkCount"], 1)
        self.assertEqual(plan["relink"][0]["location"], "/mnt/ultra-media/Qbittorrent/Films/Dune (2021)")


class QbitMappingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.close()

    async def test_torrent_mapping_includes_daily_use_fields(self):
        self.client = QBittorrentClient(QbitConfig(url="http://127.0.0.1:1", username="u", password="p"))

        async def fake_request(*args, **kwargs):
            return FakeResponse(
                [
                    {
                        "hash": VALID_HASH,
                        "name": "Example",
                        "state": "stalledDL",
                        "progress": 0.5,
                        "dlspeed": 123,
                        "upspeed": 45,
                        "ratio": 1.25,
                        "size": 1000,
                        "downloaded": 500,
                        "uploaded": 125,
                        "amount_left": 500,
                        "eta": 3600,
                        "added_on": 10,
                        "completion_on": 0,
                        "num_seeds": 4,
                        "num_leeches": 2,
                        "availability": 1.5,
                        "category": "Films",
                        "tags": "archive",
                        "save_path": "/downloads",
                        "tracker": "https://user:password@tracker.test/announce?passkey=private",
                        "priority": 1,
                    }
                ]
            )

        self.client._request = fake_request
        torrents = await self.client.torrents()
        self.assertEqual(torrents[0]["remaining"], 500)
        self.assertEqual(torrents[0]["uploaded"], 125)
        self.assertEqual(torrents[0]["eta"], 3600)
        self.assertEqual(torrents[0]["category"], "Films")
        self.assertEqual(torrents[0]["tracker"], "tracker.test")

    async def test_bulk_actions_join_hashes_for_qbittorrent(self):
        self.client = QBittorrentClient(QbitConfig(url="http://127.0.0.1:1", username="u", password="p"))
        calls = []

        async def fake_request(method, path, *, data=None, **kwargs):
            calls.append((method, path, data))
            return FakeResponse()

        self.client._request = fake_request
        await self.client.resume_many([VALID_HASH, "b" * 40])
        self.assertEqual(calls[-1][2]["hashes"], f"{VALID_HASH}|{'b' * 40}")

    async def test_force_start_sends_enabled_flag(self):
        self.client = QBittorrentClient(QbitConfig(url="http://127.0.0.1:1", username="u", password="p"))
        calls = []

        async def fake_request(method, path, *, data=None, **kwargs):
            calls.append((method, path, data))
            return FakeResponse()

        self.client._request = fake_request
        await self.client.set_force_start_many([VALID_HASH], True)
        self.assertEqual(calls[-1][1], "/api/v2/torrents/setForceStart")
        self.assertEqual(calls[-1][2]["value"], "true")

    async def test_categories_returns_save_paths_map(self):
        self.client = QBittorrentClient(QbitConfig(url="http://127.0.0.1:1", username="u", password="p"))

        async def fake_request(method, path, **kwargs):
            return FakeResponse({"Films": {"savePath": "/Qbittorrent/Films"}, "Series": {"savePath": "/Qbittorrent/Series"}})

        self.client._request = fake_request
        categories = await self.client.categories()
        self.assertEqual(categories["Films"]["savePath"], "/Qbittorrent/Films")

    async def test_set_location_sends_pipe_joined_hashes(self):
        self.client = QBittorrentClient(QbitConfig(url="http://127.0.0.1:1", username="u", password="p"))
        calls = []

        async def fake_request(method, path, *, data=None, **kwargs):
            calls.append((method, path, data))
            return FakeResponse()

        self.client._request = fake_request
        await self.client.set_location_many([VALID_HASH, "b" * 40], "/Qbittorrent/Films")
        self.assertEqual(calls[-1][1], "/api/v2/torrents/setLocation")
        self.assertEqual(calls[-1][2]["hashes"], f"{VALID_HASH}|{'b' * 40}")
        self.assertEqual(calls[-1][2]["location"], "/Qbittorrent/Films")

    async def test_set_content_layout_sends_layout_flag(self):
        self.client = QBittorrentClient(QbitConfig(url="http://127.0.0.1:1", username="u", password="p"))
        calls = []

        async def fake_request(method, path, *, data=None, **kwargs):
            calls.append((method, path, data))
            return FakeResponse()

        self.client._request = fake_request
        await self.client.set_content_layout_many([VALID_HASH], "NoSubfolder")
        self.assertEqual(calls[-1][1], "/api/v2/torrents/setContentLayout")
        self.assertEqual(calls[-1][2]["hashes"], VALID_HASH)
        self.assertEqual(calls[-1][2]["layout"], "NoSubfolder")


class MediaAutomationTests(unittest.IsolatedAsyncioTestCase):
    def build_manager(self, mount_path=None):
        temp_dir = Path(tempfile.mkdtemp())
        qbit = FakeQbit()
        manager = MediaAutomationManager(
            qbit,
            MediaAutomationConfig(
                enabled=True,
                poll_seconds=8,
                debounce_seconds=1,
                jellyfin_delay_seconds=0,
                max_rclone_retries=1,
                max_mount_retries=1,
                max_jellyfin_retries=1,
                history_limit=10,
                state_path=temp_dir / "state.json",
                mount_path=mount_path or str(temp_dir),
                rclone_refresh_mode="rc",
                rclone_rc_refresh_url="http://127.0.0.1:5572/vfs/refresh",
                rclone_rc_refresh_dir="",
                rclone_systemd_unit="",
                rclone_systemd_restart_cmd="",
                jellyfin_api_url="http://127.0.0.1:8096",
                jellyfin_api_key="token",
                jellyfin_library_map={"films": "lib-films", "series": "lib-series"},
                jellyfin_global_fallback=True,
            ),
        )
        return qbit, manager

    async def test_bootstrap_does_not_enqueue_existing_completed_torrent(self):
        qbit, manager = self.build_manager()
        qbit.torrents_payload = [{"hash": VALID_HASH, "name": "Done", "progress": 1, "completionOn": 123, "category": "films"}]
        await manager.bootstrap()
        self.assertEqual(manager.observe_torrents(qbit.torrents_payload, allow_enqueue=True), [])
        self.assertEqual(manager.snapshot()["entries"], [])

    async def test_detects_real_transition_to_complete_once(self):
        _qbit, manager = self.build_manager()
        manager.observe_torrents([{"hash": VALID_HASH, "name": "Movie", "progress": 0.5, "completionOn": 0, "category": "films"}], allow_enqueue=False)
        completed = manager.observe_torrents([{"hash": VALID_HASH, "name": "Movie", "progress": 1, "completionOn": 10, "category": "films"}], allow_enqueue=True)
        self.assertEqual(completed, [VALID_HASH])
        completed_again = manager.observe_torrents([{"hash": VALID_HASH, "name": "Movie", "progress": 1, "completionOn": 10, "category": "films"}], allow_enqueue=True)
        self.assertEqual(completed_again, [])

    async def test_groups_multiple_completions_into_single_batch(self):
        mount = str(Path(tempfile.mkdtemp()))
        _qbit, manager = self.build_manager(mount_path=mount)
        calls = []

        async def fake_refresh(**kwargs):
            calls.append(("rclone", kwargs.get("dirs")))

        async def fake_mount(**kwargs):
            calls.append("mount")

        async def fake_scan(library_ids):
            calls.append(("jellyfin", tuple(library_ids)))
            return {"scope": "targeted"}

        manager.refresh_rclone = fake_refresh
        manager.wait_for_mount = fake_mount
        manager.trigger_jellyfin_scan = fake_scan
        movie_path = os.path.join(mount, "films", "Movie")
        series_path = os.path.join(mount, "series", "Series")
        manager.observe_torrents(
            [
                {"hash": VALID_HASH, "name": "Movie", "progress": 0.2, "completionOn": 0, "category": "films", "savePath": movie_path},
                {"hash": "b" * 40, "name": "Series", "progress": 0.2, "completionOn": 0, "category": "series", "savePath": series_path},
            ],
            allow_enqueue=False,
        )
        manager.observe_torrents(
            [
                {"hash": VALID_HASH, "name": "Movie", "progress": 1, "completionOn": 10, "category": "films", "savePath": movie_path},
                {"hash": "b" * 40, "name": "Series", "progress": 1, "completionOn": 11, "category": "series", "savePath": series_path},
            ],
            allow_enqueue=True,
        )
        entries = await manager.process_pending_batch()
        self.assertEqual(len(entries), 2)
        self.assertEqual(calls[0][0], "rclone")
        self.assertEqual(calls[0][1], ["films/Movie", "series/Series"])
        self.assertEqual(calls[1], "mount")
        self.assertEqual(calls[2], ("jellyfin", ("lib-films", "lib-series")))

    async def test_retry_jellyfin_only_after_partial_failure(self):
        _qbit, manager = self.build_manager()

        async def fake_refresh(**kwargs):
            return None

        async def fake_mount(**kwargs):
            return None

        attempts = {"count": 0}

        manager.refresh_rclone = fake_refresh
        manager.wait_for_mount = fake_mount

        async def failing_then_success(library_ids):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise MediaAutomationError("Jellyfin down")
            return {"scope": "targeted"}

        manager.trigger_jellyfin_scan = failing_then_success
        manager.observe_torrents([{"hash": VALID_HASH, "name": "Movie", "progress": 0.5, "completionOn": 0, "category": "films"}], allow_enqueue=False)
        manager.observe_torrents([{"hash": VALID_HASH, "name": "Movie", "progress": 1, "completionOn": 10, "category": "films"}], allow_enqueue=True)
        entries = await manager.process_pending_batch()
        self.assertEqual(entries[0]["state"], "partial_failure")
        retried = await manager.retry(entries[0]["id"], "jellyfin")
        self.assertEqual(retried["state"], "completed")

    async def test_refresh_rclone_rc_mode_never_restarts_systemd(self):
        _qbit, manager = self.build_manager()
        calls = []

        async def fake_rc(**kwargs):
            calls.append(("rc", kwargs))

        async def fail_if_systemd(**kwargs):
            calls.append("systemd")
            raise AssertionError("systemd restart must not be triggered in rc mode")

        manager._refresh_rclone_rc = fake_rc
        manager._refresh_rclone_systemd = fail_if_systemd
        manager._config = MediaAutomationConfig(
            **{**manager._config.__dict__, "rclone_refresh_mode": "rc"},
        )
        await manager.refresh_rclone(dirs=["films/Movie"], async_run=True)
        self.assertEqual(calls, [("rc", {"dirs": ["films/Movie"], "recursive": True, "async_run": True})])

    async def test_refresh_dirs_ignores_paths_outside_mount(self):
        _qbit, manager = self.build_manager(mount_path="/mnt/ultra-media")
        dirs = manager._refresh_dirs_for_torrents(
            [
                {"hash": "a" * 40, "name": "Movie", "savePath": "/mnt/ultra-media/films/Movie"},
                {"hash": "b" * 40, "name": "Outside", "savePath": "/home/media/Outside"},
                {"hash": "c" * 40, "name": "Root", "savePath": "/mnt/ultra-media"},
                {"hash": "d" * 40, "name": "Empty", "savePath": ""},
            ]
        )
        self.assertEqual(dirs, ["films/Movie"])


class StorageQuotaTests(unittest.TestCase):
    def test_parse_ultra_quota_valid(self):
        payload = {
            "service_stats_info": {
                "free_storage_bytes": 9664750157824,
                "total_storage_unit": "G",
                "total_storage_value": 11176,
                "used_storage_value": 2175,
            }
        }
        total, used, free = _parse_ultra_quota(payload)
        self.assertEqual(total, 11176 * 1024**3)
        self.assertEqual(free, 9664750157824)
        self.assertEqual(used, total - free)

    def test_parse_ultra_quota_handles_binary_units(self):
        payload = {
            "service_stats_info": {
                "free_storage_bytes": 1024**3,
                "total_storage_unit": "TiB",
                "total_storage_value": 2,
                "used_storage_value": 1,
            }
        }
        total, _used, free = _parse_ultra_quota(payload)
        self.assertEqual(total, 2 * 1024**4)
        self.assertEqual(free, 1024**3)

    def test_parse_ultra_quota_storage_info_key(self):
        payload = {
            "Storage Info": {
                "free_storage_bytes": 104152956928,
                "total_storage_unit": "G",
                "total_storage_value": 932,
                "used_storage_unit": "G",
                "used_storage_value": 835,
            }
        }
        total, used, free = _parse_ultra_quota(payload)
        self.assertEqual(total, 932 * 1024**3)
        self.assertEqual(free, 104152956928)
        self.assertEqual(used, total - free)

    def test_parse_ultra_quota_derives_free_from_used(self):
        payload = {
            "Storage Info": {
                "total_storage_unit": "G",
                "total_storage_value": 10,
                "used_storage_unit": "G",
                "used_storage_value": 4,
            }
        }
        total, used, free = _parse_ultra_quota(payload)
        self.assertEqual(total, 10 * 1024**3)
        self.assertEqual(free, 6 * 1024**3)
        self.assertEqual(used, 4 * 1024**3)

    def test_parse_ultra_quota_returns_none_on_invalid_payload(self):
        self.assertIsNone(_parse_ultra_quota({}))
        self.assertIsNone(_parse_ultra_quota({"service_stats_info": {}}))
        self.assertIsNone(_parse_ultra_quota({"service_stats_info": {"total_storage_value": 10}}))
        self.assertIsNone(
            _parse_ultra_quota(
                {"service_stats_info": {"total_storage_value": 10, "free_storage_bytes": "abc"}}
            )
        )
        self.assertIsNone(
            _parse_ultra_quota(
                {"service_stats_info": {"total_storage_value": 10, "free_storage_bytes": 1, "total_storage_unit": "X"}}
            )
        )
        self.assertIsNone(
            _parse_ultra_quota(
                {"service_stats_info": {"total_storage_value": 10, "free_storage_bytes": 20000000000, "total_storage_unit": "G"}}
            )
        )
        self.assertIsNone(_parse_ultra_quota([]))


class UltraUrlNormalizationTests(unittest.TestCase):
    def test_strip_suffix_from_full_endpoint(self):
        from torrent_panel.config import _strip_ultra_suffix

        self.assertEqual(
            _strip_ultra_suffix("https://user.host.usbx.me/ultra-api/get-diskquota"),
            "https://user.host.usbx.me/ultra-api",
        )
        self.assertEqual(
            _strip_ultra_suffix("https://user.host.usbx.me/ultra-api/get_diskquota"),
            "https://user.host.usbx.me/ultra-api",
        )
        self.assertEqual(
            _strip_ultra_suffix("https://user.host.usbx.me/ultra-api/"),
            "https://user.host.usbx.me/ultra-api",
        )
        self.assertEqual(_strip_ultra_suffix("https://user.host.usbx.me/ultra-api"), "https://user.host.usbx.me/ultra-api")
        self.assertEqual(_strip_ultra_suffix(""), "")


class UltraQuotaFetchTests(unittest.IsolatedAsyncioTestCase):
    class FakeResponse:
        def __init__(self, payload=None, status_code=200):
            self._payload = payload if payload is not None else {}
            self.status_code = status_code

        def json(self):
            return self._payload

    def _make_client(self, responses):
        requests = []

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, headers=None):
                requests.append(url)
                return responses.get(url, UltraQuotaFetchTests.FakeResponse())

        return FakeAsyncClient, requests

    async def _fetch_with(self, url, token, client=None):
        with ExitStack() as stack:
            stack.enter_context(mock.patch("torrent_panel.services.monitoring.ULTRA_API_URL", url))
            stack.enter_context(mock.patch("torrent_panel.services.monitoring.ULTRA_API_TOKEN", token))
            if client is not None:
                stack.enter_context(mock.patch("torrent_panel.services.monitoring.httpx.AsyncClient", client))
            return await _fetch_ultra_quota()

    async def test_fetch_prefers_hyphen_path(self):
        responses = {
            "https://slot/ultra-api/get-diskquota": self.FakeResponse(
                {"Storage Info": {"free_storage_bytes": 104152956928, "total_storage_unit": "G", "total_storage_value": 932, "used_storage_unit": "G", "used_storage_value": 835}}
            )
        }
        client, requests = self._make_client(responses)
        quota, error = await self._fetch_with("https://slot/ultra-api", "token", client)
        self.assertIsNone(error)
        self.assertEqual(quota, (932 * 1024**3, 835 * 1024**3, 104152956928))
        self.assertEqual(requests, ["https://slot/ultra-api/get-diskquota"])

    async def test_fetch_falls_back_to_underscore_path(self):
        responses = {
            "https://slot/ultra-api/get-diskquota": self.FakeResponse(),
            "https://slot/ultra-api/get_diskquota": self.FakeResponse(
                {"Storage Info": {"free_storage_bytes": 10, "total_storage_unit": "G", "total_storage_value": 1}}
            ),
        }
        client, requests = self._make_client(responses)
        quota, error = await self._fetch_with("https://slot/ultra-api", "token", client)
        self.assertIsNone(error)
        self.assertEqual(quota, (1024**3, 1024**3 - 10, 10))
        self.assertEqual(requests, ["https://slot/ultra-api/get-diskquota", "https://slot/ultra-api/get_diskquota"])

    async def test_fetch_reports_http_error(self):
        responses = {
            "https://slot/ultra-api/get-diskquota": self.FakeResponse(status_code=404),
            "https://slot/ultra-api/get_diskquota": self.FakeResponse(status_code=404),
        }
        client, requests = self._make_client(responses)
        quota, error = await self._fetch_with("https://slot/ultra-api", "token", client)
        self.assertIsNone(quota)
        self.assertEqual(error, "http_404")
        self.assertEqual(len(requests), 2)

    async def test_fetch_not_configured(self):
        quota, error = await self._fetch_with("", "")
        self.assertIsNone(quota)
        self.assertEqual(error, "not_configured")


class StorageSnapshotQuotaTests(unittest.IsolatedAsyncioTestCase):
    """storage_snapshot must only ever report the slot quota, never the server disk."""

    def _patch_rclone(self):
        class FakeResponse:
            status_code = 200

            def json(self):
                return {}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, json=None):
                return FakeResponse()

        return mock.patch("torrent_panel.services.monitoring.httpx.AsyncClient", FakeAsyncClient)

    async def test_quota_ok_reports_ultra_api(self):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch(
                    "torrent_panel.services.monitoring._fetch_ultra_quota",
                    mock.AsyncMock(return_value=((932 * 1024**3, 835 * 1024**3, 104152956928), None)),
                )
            )
            stack.enter_context(self._patch_rclone())
            result = await storage_snapshot(mock.Mock())
        disk = result["disk"]
        self.assertEqual(disk["source"], "ultra-api")
        self.assertTrue(disk["available"])
        self.assertEqual(disk["totalBytes"], 932 * 1024**3)
        self.assertEqual(disk["usedBytes"], 835 * 1024**3)

    async def test_quota_failure_is_unavailable_not_server(self):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch(
                    "torrent_panel.services.monitoring._fetch_ultra_quota",
                    mock.AsyncMock(return_value=(None, "timeout")),
                )
            )
            stack.enter_context(self._patch_rclone())
            result = await storage_snapshot(mock.Mock())
        disk = result["disk"]
        self.assertFalse(disk["available"])
        self.assertEqual(disk["source"], "unavailable")
        self.assertEqual(disk["totalBytes"], 0)
        self.assertEqual(disk["usedBytes"], 0)
        self.assertEqual(disk["quotaError"], "timeout")

    async def test_quota_not_configured_is_unavailable(self):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch(
                    "torrent_panel.services.monitoring._fetch_ultra_quota",
                    mock.AsyncMock(return_value=(None, "not_configured")),
                )
            )
            stack.enter_context(self._patch_rclone())
            result = await storage_snapshot(mock.Mock())
        disk = result["disk"]
        self.assertFalse(disk["available"])
        self.assertEqual(disk["source"], "unavailable")
        self.assertEqual(disk["quotaError"], "not_configured")
        self.assertEqual(disk["totalBytes"], 0)

    async def test_quota_failure_never_calls_disk_usage(self):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch(
                    "torrent_panel.services.monitoring._fetch_ultra_quota",
                    mock.AsyncMock(return_value=(None, "http_500")),
                )
            )
            stack.enter_context(self._patch_rclone())
            result = await storage_snapshot(mock.Mock())
        self.assertEqual(result["disk"]["source"], "unavailable")
        self.assertEqual(result["disk"]["totalBytes"], 0)
        self.assertEqual(result["disk"]["usedBytes"], 0)
        self.assertFalse(result["disk"]["available"])

    def test_disk_unavailable_payload(self):
        payload = _disk_unavailable("connection")
        self.assertFalse(payload["available"])
        self.assertEqual(payload["source"], "unavailable")
        self.assertEqual(payload["totalBytes"], 0)
        self.assertEqual(payload["quotaError"], "connection")

    def test_disk_snapshot_available_flag(self):
        payload = _disk_snapshot(1000, 400, "ultra-api")
        self.assertTrue(payload["available"])
        self.assertEqual(payload["source"], "ultra-api")


if __name__ == "__main__":
    unittest.main()
