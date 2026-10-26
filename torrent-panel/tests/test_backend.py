import asyncio
import os
import tempfile
import sys
import threading
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
from torrent_panel.qbittorrent import AsyncTTLCache, QBittorrentClient, QbitConfig, QbitError  # noqa: E402
from torrent_panel.routes import dashboard as dashboard_routes  # noqa: E402
from torrent_panel.routes import torrents as torrent_routes  # noqa: E402
from torrent_panel.services.monitoring import (
    _disk_unavailable,
    storage_snapshot,
)  # noqa: E402
from torrent_panel.services import relink as relink_service  # noqa: E402
from torrent_panel.services import organizer as organizer_service  # noqa: E402
from torrent_panel import config as panel_config  # noqa: E402
from torrent_panel.services.ratio_monitor import MAX_THRESHOLD, MIN_THRESHOLD, RatioMonitor, RatioThresholdError  # noqa: E402
from torrent_panel.services.stats import StatsStore  # noqa: E402
from torrent_panel.services.tracker_stats import TrackerStatsStore  # noqa: E402
from torrent_panel.services.organization_runs import OrganizationRunStore  # noqa: E402
from torrent_panel.services.metadata import MediaMetadataResolver  # noqa: E402
from torrent_panel.services.notifications import NotificationCenter  # noqa: E402
from torrent_panel.services.automations import AutomationRuleStore  # noqa: E402
from torrent_panel.services import metadata as metadata_service  # noqa: E402
from torrent_panel.services.live import _stable_fingerprint  # noqa: E402
from torrent_panel.routes.jobs import JobRequest, _runner  # noqa: E402


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
        self.fail_location_hashes: set[str] = set()
        self.fail_rename_old_paths: set[str] = set()

    async def torrents(self):
        return list(self.torrents_payload)

    async def categories(self):
        return dict(self.categories_payload)

    async def files(self, torrent_hash):
        self.calls.append(("files", torrent_hash))
        return list(self.files_payload.get(torrent_hash, []))

    async def pause_many(self, hashes):
        self.calls.append(("pause", list(hashes)))
        for torrent in self.torrents_payload:
            if torrent.get("hash") not in hashes:
                continue
            state = str(torrent.get("state") or "")
            if state not in {"missingFiles", "error"}:
                torrent["state"] = "stoppedUP" if state in {"uploading", "stalledUP", "forcedUP", "queuedUP", "checkingUP"} else "stoppedDL"

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
        if self.fail_location_hashes and self.fail_location_hashes.intersection(hashes):
            raise QbitError(
                404,
                "Torrent inconnu ou action qBittorrent indisponible.",
                code="qbit_action_unavailable",
                recovery="Réessayer",
            )

    async def ensure_category(self, category, save_path=""):
        self.calls.append(("ensure_category", category, save_path))
        if category and category not in self.categories_payload:
            self.categories_payload[category] = {"name": category, "savePath": save_path}

    async def set_category_many(self, hashes, category):
        self.calls.append(("set_category", list(hashes), category))

    async def rename_file(self, torrent_hash, old_path, new_path):
        self.calls.append(("rename_file", torrent_hash, old_path, new_path))
        if old_path in self.fail_rename_old_paths:
            self.fail_rename_old_paths.discard(old_path)
            raise QbitError(409, "Renommage qBittorrent refusé.")
        for item in self.files_payload.get(torrent_hash, []):
            if item.get("name") == old_path:
                item["name"] = new_path
                break

    async def rename_folder(self, torrent_hash, old_path, new_path):
        self.calls.append(("rename_folder", torrent_hash, old_path, new_path))
        for item in self.files_payload.get(torrent_hash, []):
            name = str(item.get("name") or "")
            if name == old_path:
                item["name"] = new_path
            elif name.startswith(old_path + "/"):
                item["name"] = new_path + name[len(old_path):]

    async def set_content_layout_many(self, hashes, layout):
        self.calls.append(("set_content_layout", list(hashes), layout))

    async def recheck_many(self, hashes):
        self.calls.append(("recheck", list(hashes)))

    async def ready(self):
        return True

    async def webapi_version(self):
        return "2.11.4"

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
        self.original_verified_resume = app.state.verified_resume
        self.original_organization_runs = app.state.organization_runs
        self.original_metadata_resolver = app.state.metadata_resolver
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
        app.state.verified_resume = organizer_service.VerifiedResumeManager(
            app.state.qbit,
            Path(tempfile.mkdtemp()) / "organizer-resume.json",
            poll_seconds=2,
        )
        app.state.organization_runs = OrganizationRunStore(Path(tempfile.mkdtemp()) / "runs.json")
        app.state.metadata_resolver = MediaMetadataResolver()
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
        app.state.verified_resume = self.original_verified_resume
        app.state.organization_runs = self.original_organization_runs
        app.state.metadata_resolver = self.original_metadata_resolver
        app.state.csrf_tokens = self.original_csrf_tokens
        torrent_routes.TR4KER_ANNOUNCE_URL = self.original_tr4ker_announce_url

    def post_action(self, path, payload):
        return self.client.post(
            path,
            json=payload,
            headers={"X-Torrent-Panel-CSRF": self.csrf},
        )

    def test_tracker_index_cache_accepts_supplied_torrents(self):
        torrent_routes.invalidate_tracker_index()
        torrent = {"hash": VALID_HASH, "name": "Ubuntu", "tracker": ""}
        app.state.qbit.trackers_payload = [{"url": "https://tracker.test/announce"}]
        request = mock.Mock(app=app)
        first = asyncio.run(torrent_routes._tracker_index_payload(request, torrents=[torrent]))
        second = asyncio.run(torrent_routes._tracker_index_payload(request, torrents=[torrent]))
        self.assertEqual(first, second)
        self.assertEqual([call for call in app.state.qbit.calls if call[0] == "trackers"], [("trackers", VALID_HASH)])

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

    def test_set_category_creates_missing_library_category(self):
        with mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", "/home/micilico/downloads/qbittorrent"):
            response = self.post_action(
                "/torrent-panel/api/torrents/set-category",
                {"hashes": [VALID_HASH], "category": "Jeux"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            ("ensure_category", "Jeux", "/home/micilico/downloads/qbittorrent/Jeux"),
            app.state.qbit.calls,
        )
        self.assertIn(("set_category", [VALID_HASH], "Jeux"), app.state.qbit.calls)

    def test_live_fingerprint_ignores_collection_timestamps_only(self):
        first = {
            "observedAt": "2026-10-26T10:00:00Z",
            "torrents": [{"hash": VALID_HASH, "progress": 0.5, "date": "2026-10-26"}],
        }
        second = {
            "observedAt": "2026-10-26T10:00:01Z",
            "torrents": [{"hash": VALID_HASH, "progress": 0.5, "date": "2026-10-26"}],
        }
        self.assertEqual(_stable_fingerprint(first), _stable_fingerprint(second))
        second["torrents"][0]["progress"] = 0.6
        self.assertNotEqual(_stable_fingerprint(first), _stable_fingerprint(second))
        second["torrents"][0]["progress"] = 0.5
        second["torrents"][0]["date"] = "2026-10-27"
        self.assertNotEqual(_stable_fingerprint(first), _stable_fingerprint(second))

    def test_library_apply_job_preserves_run_id_validation(self):
        request = mock.Mock(app=app)
        _key, runner = _runner(
            request,
            JobRequest(
                type="library-apply",
                params={"runId": "missing-run-0001", "hashes": [VALID_HASH]},
            ),
        )
        context = mock.Mock()
        context.report = mock.Mock()
        with self.assertRaises(Exception) as error:
            asyncio.run(runner(context))
        self.assertEqual(getattr(error.exception, "status_code", None), 404)
        self.assertEqual(app.state.qbit.calls, [])

    def test_jobs_reject_invalid_hashes_before_queueing(self):
        response = self.post_action(
            "/torrent-panel/api/jobs",
            {"type": "organize", "params": {"hashes": ["invalid"]}},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "job_invalid")

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


class PerformanceCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_ttl_cache_coalesces_and_invalidates(self):
        cache = AsyncTTLCache(60)
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return calls

        self.assertEqual(await asyncio.gather(cache.get_or_set(factory), cache.get_or_set(factory)), [1, 1])
        self.assertEqual(calls, 1)
        self.assertEqual(await cache.get_or_set(factory), 1)
        cache.invalidate()
        self.assertEqual(await cache.get_or_set(factory), 2)

    async def test_invalidation_never_returns_a_previously_stored_value(self):
        cache = AsyncTTLCache(10 ** 9)
        values = iter([1, 2])

        async def factory():
            return next(values)

        self.assertEqual(await cache.get_or_set(factory), 1)
        cache.invalidate()
        self.assertEqual(await cache.get_or_set(factory), 2)

    async def test_failed_read_is_not_cached_and_can_retry(self):
        cache = AsyncTTLCache(60)
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            return "ok"

        with self.assertRaises(RuntimeError):
            await cache.get_or_set(factory)
        self.assertEqual(await cache.get_or_set(factory), "ok")
        self.assertEqual(calls, 2)

    async def test_cancelled_waiter_does_not_cancel_the_shared_read(self):
        cache = AsyncTTLCache(60)
        calls = 0
        release = asyncio.Event()

        async def factory():
            nonlocal calls
            calls += 1
            await release.wait()
            return "value"

        cancelled = asyncio.create_task(cache.get_or_set(factory))
        survivor = asyncio.create_task(cache.get_or_set(factory))
        await asyncio.sleep(0.05)

        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled

        release.set()
        self.assertEqual(await survivor, "value")
        self.assertEqual(calls, 1)

    async def test_invalidation_during_a_read_is_not_stored(self):
        cache = AsyncTTLCache(60)
        values = iter(["before", "after"])

        async def factory():
            return next(values)

        self.assertEqual(await cache.get_or_set(factory), "before")
        cache.invalidate()
        self.assertEqual(await cache.get_or_set(factory), "after")
        self.assertEqual(await cache.get_or_set(factory), "after")

    def test_repeated_state_observation_does_not_save(self):
        state_dir = Path(tempfile.mkdtemp())
        notification = NotificationCenter(state_dir / "notifications.json")
        notification._save = mock.Mock()
        alert = {"code": "same", "service": "qBittorrent", "severity": "warning", "message": "Stable", "date": "2026-08-17T00:00:00Z"}
        notification.reconcile([alert])
        notification.reconcile([alert])
        self.assertEqual(notification._save.call_count, 1)

        rules = AutomationRuleStore(state_dir / "rules.json")
        rules._save = mock.Mock()
        rules.simulate({"alerts": [], "services": []})
        self.assertEqual(rules._save.call_count, 0)

        stats = StatsStore(state_dir / "stats.json", history_days=7, persist_debounce_seconds=60)
        stats._save = mock.Mock()
        torrent = [{"hash": VALID_HASH, "downloaded": 100, "uploaded": 10, "state": "downloading"}]
        stats.observe(torrent)
        stats.observe(torrent)
        self.assertEqual(stats._save.call_count, 1)

        tracker = TrackerStatsStore(state_dir / "tracker.json", persist_debounce_seconds=60)
        tracker._save = mock.Mock()
        tracker.observe(torrent, {})
        tracker.observe(torrent, {})
        self.assertEqual(tracker._save.call_count, 1)


class RelinkScanConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_runs_off_loop_and_concurrent_misses_share_one_scan(self):
        original_cache = relink_service._SCAN_CACHE.copy()
        relink_service._SCAN_CACHE.update(ts=0.0, data=None, key=None)
        self.addCleanup(relink_service._SCAN_CACHE.update, original_cache)
        started = threading.Event()
        release = threading.Event()
        walk_calls = []
        mount_root = "/tmp/relink-scan-concurrency"
        canonical_mount = os.path.realpath(mount_root)
        qbit_root = "/downloads/qbittorrent"

        def slow_walk(root):
            walk_calls.append(root)
            if root == canonical_mount:
                return iter([(root, ["qbittorrent"], [])])
            started.set()
            release.wait(2)
            return iter([(os.path.join(root, "Films"), [], ["example.mkv"])])

        with (
            mock.patch.object(relink_service.os, "walk", side_effect=slow_walk),
            mock.patch.object(relink_service.os.path, "getsize", return_value=10),
        ):
            tasks = [
                asyncio.create_task(relink_service._scan_index_async(mount_root, qbit_root))
                for _ in range(3)
            ]
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            # The simulated FUSE scan is still blocked while the event loop runs.
            await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.2)
            release.set()
            results = await asyncio.gather(*tasks)

        self.assertEqual(walk_calls, [canonical_mount, os.path.join(canonical_mount, "qbittorrent")])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        self.assertEqual(results[0]["files"], {"example.mkv": {"qbittorrent/Films": 10}})


class MetadataResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_jellyfin_and_tmdb_result_is_not_ambiguous(self):
        resolver = MediaMetadataResolver()
        resolver._jellyfin = mock.AsyncMock(return_value=[
            {"title": "House of the Dragon", "year": 2022, "source": "jellyfin"},
        ])
        resolver._tmdb = mock.AsyncMock(return_value=[
            {"title": "House of the Dragon", "year": "2022", "source": "tmdb"},
        ])

        with (
            mock.patch.object(metadata_service, "JELLYFIN_API_KEY", "configured"),
            mock.patch.object(metadata_service, "TMDB_API_KEY", "configured"),
        ):
            result = await resolver._resolve_uncached("House Of The Dragon", "series", None)

        self.assertEqual(result["confidence"], "certain")
        self.assertEqual(result["source"], "jellyfin")

    async def test_different_close_results_remain_ambiguous(self):
        resolver = MediaMetadataResolver()
        resolver._jellyfin = mock.AsyncMock(return_value=[
            {"title": "The Office", "year": 2005, "source": "jellyfin"},
        ])
        resolver._tmdb = mock.AsyncMock(return_value=[
            {"title": "The Office", "year": 1995, "source": "tmdb"},
        ])

        with (
            mock.patch.object(metadata_service, "JELLYFIN_API_KEY", "configured"),
            mock.patch.object(metadata_service, "TMDB_API_KEY", "configured"),
        ):
            result = await resolver._resolve_uncached("The Office", "series", None)

        self.assertEqual(result["confidence"], "ambiguous")


class RelinkTests(BackendTests):
    QBIT_ROOT = "/home/micilico/downloads/qbittorrent"

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
        self._original_qbit_root = relink_service.QBIT_SAVE_PATH
        self._original_config_qbit_root = panel_config.QBIT_SAVE_PATH
        relink_service.MEDIA_MOUNT_PATH = str(self.mount)
        relink_service.QBIT_SAVE_PATH = self.QBIT_ROOT
        panel_config.QBIT_SAVE_PATH = self.QBIT_ROOT
        relink_service._SCAN_CACHE["data"] = None
        relink_service._SCAN_CACHE["ts"] = 0.0

    def tearDown(self):
        relink_service.MEDIA_MOUNT_PATH = self._original_mount
        relink_service.QBIT_SAVE_PATH = self._original_qbit_root
        panel_config.QBIT_SAVE_PATH = self._original_config_qbit_root
        relink_service._SCAN_CACHE["data"] = None
        relink_service._SCAN_CACHE["ts"] = 0.0
        super().tearDown()

    def build_payload(self):
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Dune.2021.1080p",
                "state": "pausedDL",
                "category": "Films",
                "savePath": "/home/micilico/downloads/qbittorrent",
                "contentPath": "/home/micilico/downloads/qbittorrent/Dune.2021.1080p",
            },
            {
                "hash": "b" * 40,
                "name": "Show.S01",
                "state": "missingFiles",
                "category": "Series",
                "savePath": "/home/micilico/downloads/qbittorrent",
                "contentPath": "/home/micilico/downloads/qbittorrent/Show.S01",
            },
            {
                "hash": "c" * 40,
                "name": "Déjà aligné",
                "state": "uploading",
                "category": "Films",
                "savePath": "/home/micilico/downloads/qbittorrent/Films/Dune (2021)/Dune.2021.1080p",
            },
            {"hash": "d" * 40, "name": "Hors racine", "state": "uploading", "category": "", "savePath": "/old/downloads/Other"},
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
        self.assertEqual(plan["skippedCount"], 0)
        self.assertEqual(plan["layout"], "Subfolder")
        locations = sorted(group["location"] for group in plan["relink"])
        self.assertEqual(
            locations,
            [
                "/home/micilico/downloads/qbittorrent/Films/Dune (2021)",
                "/home/micilico/downloads/qbittorrent/Series/Show/Saison 1",
            ],
        )

    def test_apply_pauses_then_relinks_with_subfolder(self):
        self.build_payload()
        response = self.post_action("/torrent-panel/api/torrents/relink", {})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["relinked"], 2)
        self.assertEqual(body["result"]["paused"], 2)
        self.assertEqual(body["result"]["rechecked"], 0)
        calls = app.state.qbit.calls
        self.assertIn(("pause", [VALID_HASH, "b" * 40]), calls)
        self.assertLess(calls.index(("pause", [VALID_HASH, "b" * 40])), calls.index(("set_location", [VALID_HASH], "/home/micilico/downloads/qbittorrent/Films/Dune (2021)")))
        self.assertIn(("set_location", [VALID_HASH], "/home/micilico/downloads/qbittorrent/Films/Dune (2021)"), calls)
        self.assertIn(("set_location", ["b" * 40], "/home/micilico/downloads/qbittorrent/Series/Show/Saison 1"), calls)
        self.assertIn(("set_content_layout", [VALID_HASH], "Subfolder"), calls)
        self.assertIn(("set_content_layout", ["b" * 40], "Subfolder"), calls)
        self.assertNotIn(("recheck", [VALID_HASH, "b" * 40]), calls)
        self.assertNotIn("recheck", [call[0] for call in calls])

    def test_apply_recheck_only_when_requested(self):
        self.build_payload()
        response = self.post_action("/torrent-panel/api/torrents/relink", {"recheck": True})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["relinked"], 2)
        self.assertEqual(body["result"]["rechecked"], 2)
        calls = app.state.qbit.calls
        self.assertEqual(calls[-1], ("recheck", [VALID_HASH, "b" * 40]))

    def test_apply_renames_before_set_location(self):
        from torrent_panel.services import cloud_panel as cloud_panel_service

        self.build_payload()
        renamed: list[dict[str, str]] = []
        calls = app.state.qbit.calls

        async def fake_arrange_batch(items):
            renamed.extend(items)
            return {"results": [{"success": True} for _ in items], "failed": 0}

        import unittest.mock as _mock

        with _mock.patch.object(cloud_panel_service, "arrange_batch", side_effect=fake_arrange_batch):
            response = self.post_action("/torrent-panel/api/torrents/relink", {})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["relinked"], 2)
        self.assertEqual(renamed, [])
        self.assertIn(("set_content_layout", [VALID_HASH], "Subfolder"), calls)

    def test_apply_renames_folder_and_files_for_renamed_torrent(self):
        import shutil

        from torrent_panel.services import cloud_panel as cloud_panel_service

        relink_service.QBIT_SAVE_PATH = "/home/micilico/downloads/qbittorrent"
        panel_config.QBIT_SAVE_PATH = "/home/micilico/downloads/qbittorrent"
        qbit_setup_dir = self.mount / "Qbittorrent"
        if qbit_setup_dir.exists():
            shutil.rmtree(qbit_setup_dir)
        film_dir = self.mount / "qbittorrent" / "Films" / "Backrooms (2026)"
        film_dir.mkdir(parents=True, exist_ok=True)
        (film_dir / "Backrooms.2026.MULTi.CA.2160p.WEB.H265-SUPPLY.mkv").write_bytes(b"0123456789")
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
            VALID_HASH: [{"name": "Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY.mkv", "size": 10}],
        }
        relink_service._SCAN_CACHE["data"] = None
        renamed: list[dict[str, str]] = []

        async def fake_arrange_batch(items):
            renamed.extend(items)
            return {"results": [{"success": True} for _ in items], "failed": 0}

        import unittest.mock as _mock

        with _mock.patch.object(cloud_panel_service, "arrange_batch", side_effect=fake_arrange_batch):
            response = self.post_action("/torrent-panel/api/torrents/relink", {})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["relinked"], 1)
        self.assertIn(
            {"op": "rename", "path": "qbittorrent/Films/Backrooms (2026)", "old_name": "Backrooms.2026.MULTi.CA.2160p.WEB.H265-SUPPLY.mkv", "new_name": "Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY.mkv"},
            renamed,
        )
        self.assertIn(
            {"op": "rename", "path": "qbittorrent/Films", "old_name": "Backrooms (2026)", "new_name": "Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY"},
            renamed,
        )
        relink_service.QBIT_SAVE_PATH = ""
        panel_config.QBIT_SAVE_PATH = ""

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
        self.assertIn(("set_location", [VALID_HASH], "/home/micilico/downloads/qbittorrent/Films/Dune (2021)"), app.state.qbit.calls)

    def test_apply_isolates_stale_hash_when_setlocation_returns_404(self):
        app.state.qbit.torrents_payload = [
            {"hash": VALID_HASH, "name": "Dune.2021.1080p"},
            {"hash": "b" * 40, "name": "Show.S01"},
        ]
        app.state.qbit.fail_location_hashes = {"b" * 40}
        plan = {
            "relink": [
                {
                    "location": "/home/micilico/downloads/qbittorrent/Films/Dune (2021)",
                    "layout": "Subfolder",
                    "hashes": [VALID_HASH, "b" * 40],
                    "names": ["Dune.2021.1080p", "Show.S01"],
                    "entries": [
                        {"hash": VALID_HASH, "name": "Dune.2021.1080p", "renames": []},
                        {"hash": "b" * 40, "name": "Show.S01", "renames": []},
                    ],
                }
            ],
            "recheckOnly": [],
        }
        result = asyncio.run(relink_service.apply_relink_plan(app.state.qbit, plan, recheck=False))
        self.assertEqual(result["relinked"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["failures"][0]["hash"], "b" * 40)
        self.assertEqual(result["failures"][0]["message"], "Torrent inconnu côté qBittorrent.")
        calls = [call for call in app.state.qbit.calls if call[0] == "set_location"]
        self.assertIn(("set_location", [VALID_HASH], "/home/micilico/downloads/qbittorrent/Films/Dune (2021)"), calls)

    def test_apply_skips_torrent_removed_since_plan_build(self):
        app.state.qbit.torrents_payload = [
            {"hash": VALID_HASH, "name": "Dune.2021.1080p"},
        ]
        plan = {
            "relink": [
                {
                    "location": "/home/micilico/downloads/qbittorrent/Films/Dune (2021)",
                    "layout": "Subfolder",
                    "hashes": [VALID_HASH, "b" * 40],
                    "names": ["Dune.2021.1080p", "Show.S01"],
                    "entries": [
                        {"hash": VALID_HASH, "name": "Dune.2021.1080p", "renames": []},
                        {"hash": "b" * 40, "name": "Show.S01", "renames": []},
                    ],
                }
            ],
            "recheckOnly": [],
        }
        result = asyncio.run(relink_service.apply_relink_plan(app.state.qbit, plan, recheck=False))
        self.assertEqual(result["relinked"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["failures"][0]["hash"], "b" * 40)
        self.assertEqual(result["failures"][0]["message"], "Torrent inconnu côté qBittorrent.")
        self.assertEqual(app.state.qbit.calls[0], ("pause", [VALID_HASH]))
        calls = [call for call in app.state.qbit.calls if call[0] == "set_location"]
        self.assertEqual(calls, [("set_location", [VALID_HASH], "/home/micilico/downloads/qbittorrent/Films/Dune (2021)")])

    def test_locates_files_directly_when_no_preserved_folder(self):
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
                "savePath": "/home/micilico/downloads/qbittorrent",
                "contentPath": "/home/micilico/downloads/qbittorrent/autre-nom",
            },
        ]
        app.state.qbit.files_payload = {VALID_HASH: [{"name": "Dune.mkv", "size": 4}]}
        relink_service._SCAN_CACHE["data"] = None
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["relinkCount"], 1)
        self.assertEqual(plan["relink"][0]["location"], "/home/micilico/downloads/qbittorrent/Films")

    def test_no_relink_when_nothing_affected(self):
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Sain",
                "state": "uploading",
                "category": "Films",
                "savePath": "/home/micilico/downloads/qbittorrent/Films/Dune (2021)/Dune.2021.1080p",
            },
        ]
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["total"], 0)
        self.assertEqual(plan["relinkCount"], 0)

    def test_relink_status_counts_missing_and_shallow_roots(self):
        app.state.qbit.torrents_payload = [
            {"hash": VALID_HASH, "name": "Manquant", "state": "missingFiles", "category": "Films", "savePath": "/old/thing"},
            {"hash": "b" * 40, "name": "Racine qbit", "state": "uploading", "category": "Films", "savePath": "/home/micilico/downloads/qbittorrent"},
            {"hash": "c" * 40, "name": "Dans Films", "state": "uploading", "category": "Films", "savePath": "/home/micilico/downloads/qbittorrent/Films/Dune (2021)"},
            {"hash": "e" * 40, "name": "En pause", "state": "stoppedDL", "category": "Films", "savePath": "/home/micilico/downloads/qbittorrent/Films/Dune (2021)/Dune.2021.1080p"},
        ]
        response = self.client.get("/torrent-panel/api/torrents/relink-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 3)

    def test_relink_plan_anchors_via_qbit_root_subfolder(self):
        """Scénario réel : mount contient qbittorrent/Films, torrent à la racine qBittorrent."""
        import shutil

        qbit_setup_dir = self.mount / "Qbittorrent"
        if qbit_setup_dir.exists():
            shutil.rmtree(qbit_setup_dir)
        film_dir = self.mount / "qbittorrent" / "Films" / "Dune (2021)"
        film_dir.mkdir(parents=True, exist_ok=True)
        (film_dir / "Dune.mkv").write_bytes(b"keep")
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
        self.assertEqual(plan["relink"][0]["location"], "/home/micilico/downloads/qbittorrent/Films")

    def test_relink_locates_renamed_files_in_organized_folder(self):
        """Cas réel : fichiers renommés par le rangement + catégorie prowlarr mais contenu rangé dans Films."""
        import shutil

        qbit_setup_dir = self.mount / "Qbittorrent"
        if qbit_setup_dir.exists():
            shutil.rmtree(qbit_setup_dir)
        film_dir = self.mount / "qbittorrent" / "Films" / "Backrooms (2026)"
        film_dir.mkdir(parents=True, exist_ok=True)
        (film_dir / "Backrooms.2026.MULTi.CA.2160p.WEB.H265-SUPPLY.mkv").write_bytes(b"0123456789")
        (film_dir / "Backrooms.2026.MULTi.CA.2160p.WEB.H265-SUPPLY.nfo").write_bytes(b"nfo")
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
        group = plan["relink"][0]
        self.assertEqual(group["location"], "/home/micilico/downloads/qbittorrent/Films")
        self.assertEqual(
            group["entries"][0]["renames"],
            [
                {"op": "rename", "path": "qbittorrent/Films/Backrooms (2026)", "old_name": "Backrooms.2026.MULTi.CA.2160p.WEB.H265-SUPPLY.mkv", "new_name": "Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY.mkv"},
                {"op": "rename", "path": "qbittorrent/Films/Backrooms (2026)", "old_name": "Backrooms.2026.MULTi.CA.2160p.WEB.H265-SUPPLY.nfo", "new_name": "Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY.nfo"},
                {"op": "rename", "path": "qbittorrent/Films", "old_name": "Backrooms (2026)", "new_name": "Backrooms.2026.MULTi.VFQ.2160p.WEB.10bits.EAC3.5.1.H265-SUPPLY"},
            ],
        )

    def test_relink_plan_includes_paused_downloads(self):
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
                "savePath": "/home/micilico/downloads/qbittorrent",
                "contentPath": "/home/micilico/downloads/qbittorrent/Films/Dune.2021.1080p",
            },
        ]
        app.state.qbit.files_payload = {VALID_HASH: [{"name": "Dune.mkv", "size": 4}]}
        relink_service._SCAN_CACHE["data"] = None
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["relinkCount"], 1)
        self.assertEqual(plan["relink"][0]["location"], "/home/micilico/downloads/qbittorrent/Films")

    def test_relink_download_root_is_never_renamed(self):
        root = self.mount / "Qbittorrent"
        root.mkdir(parents=True, exist_ok=True)
        (root / "Root.Movie.Renamed.mkv").write_bytes(b"root-movie-unique")
        app.state.qbit.torrents_payload = [{
            "hash": VALID_HASH,
            "name": "Root.Movie.Torrent",
            "state": "missingFiles",
            "category": "Films",
            "savePath": "/home/micilico/downloads/qbittorrent",
            "contentPath": "/home/micilico/downloads/qbittorrent/Root.Movie.Torrent",
        }]
        for multi in (False, True):
            with self.subTest(multi=multi):
                specs = [{"name": "Root.Movie.mkv", "size": 17}]
                if multi:
                    (root / "Root.Movie.srt").write_bytes(b"root-subtitle-unique")
                    specs.append({"name": "Root.Movie.srt", "size": 20})
                app.state.qbit.files_payload = {VALID_HASH: specs}
                relink_service._SCAN_CACHE["data"] = None
                response = self.client.get("/torrent-panel/api/torrents/relink-preview")
                self.assertEqual(response.status_code, 200)
                plan = response.json()["plan"]
                self.assertEqual(plan["relinkCount"], 1)
                group = plan["relink"][0]
                self.assertEqual(group["location"], "/home/micilico/downloads/qbittorrent")
                self.assertEqual(group["layout"], "Subfolder" if multi else "NoSubfolder")
                ops = group["entries"][0]["renames"]
                self.assertFalse(any(op.get("old_name") == "Qbittorrent" for op in ops))
                if multi:
                    self.assertEqual(ops[0]["op"], "mkdir")
                    self.assertEqual(sum(op["op"] == "move" for op in ops), 2)
                else:
                    self.assertEqual(ops, [{"op": "rename", "path": "Qbittorrent", "old_name": "Root.Movie.Renamed.mkv", "new_name": "Root.Movie.mkv"}])

    def test_single_file_in_category_root_stays_flat(self):
        """Cas B mono-fichier : fichier directement dans Films → rename seul (pas de mkdir/move), layout NoSubfolder."""
        import shutil

        qbit_setup_dir = self.mount / "Qbittorrent"
        if qbit_setup_dir.exists():
            shutil.rmtree(qbit_setup_dir)
        films_dir = self.mount / "qbittorrent" / "Films"
        films_dir.mkdir(parents=True, exist_ok=True)
        (films_dir / "Film.2026.MULTi.CA.mkv").write_bytes(b"0123456789")
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Film.2026.MULTi.VFQ.2160p.H265-SUPPLY",
                "state": "pausedDL",
                "category": "prowlarr",
                "savePath": "/home/micilico/downloads/qbittorrent",
                "contentPath": "/home/micilico/downloads/qbittorrent/Film.2026.MULTi.VFQ.2160p.H265-SUPPLY",
            },
        ]
        app.state.qbit.files_payload = {VALID_HASH: [{"name": "Film.2026.MULTi.VFQ.2160p.H265-SUPPLY.mkv", "size": 10}]}
        relink_service._SCAN_CACHE["data"] = None
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["relinkCount"], 1)
        group = plan["relink"][0]
        self.assertEqual(group["location"], "/home/micilico/downloads/qbittorrent/Films")
        self.assertEqual(group["layout"], "NoSubfolder")
        ops = group["entries"][0]["renames"]
        self.assertEqual(
            ops,
            [
                {"op": "rename", "path": "qbittorrent/Films", "old_name": "Film.2026.MULTi.CA.mkv", "new_name": "Film.2026.MULTi.VFQ.2160p.H265-SUPPLY.mkv"},
            ],
        )
        self.assertFalse(any(op.get("op") == "mkdir" for op in ops))
        self.assertFalse(any(op.get("op") == "rename" and op.get("old_name") == "Films" for op in ops))

    def test_multiple_files_in_category_root_create_torrent_subfolder(self):
        """Cas B multi-fichiers : plusieurs fichiers dans Films → mkdir Films/<torrent> + move, jamais renommer Films."""
        import shutil

        qbit_setup_dir = self.mount / "Qbittorrent"
        if qbit_setup_dir.exists():
            shutil.rmtree(qbit_setup_dir)
        films_dir = self.mount / "qbittorrent" / "Films"
        films_dir.mkdir(parents=True, exist_ok=True)
        (films_dir / "Film.2026.MULTi.CA.mkv").write_bytes(b"0123456789")
        (films_dir / "Film.2026.MULTi.CA.srt").write_bytes(b"srt")
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Film.2026.MULTi.VFQ.2160p.H265-SUPPLY",
                "state": "pausedDL",
                "category": "prowlarr",
                "savePath": "/home/micilico/downloads/qbittorrent",
                "contentPath": "/home/micilico/downloads/qbittorrent/Film.2026.MULTi.VFQ.2160p.H265-SUPPLY",
            },
        ]
        app.state.qbit.files_payload = {
            VALID_HASH: [
                {"name": "Film.2026.MULTi.VFQ.2160p.H265-SUPPLY.mkv", "size": 10},
                {"name": "Film.2026.MULTi.VFQ.2160p.H265-SUPPLY.srt", "size": 3},
            ]
        }
        relink_service._SCAN_CACHE["data"] = None
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["relinkCount"], 1)
        group = plan["relink"][0]
        self.assertEqual(group["location"], "/home/micilico/downloads/qbittorrent/Films")
        self.assertEqual(group["layout"], "Subfolder")
        ops = group["entries"][0]["renames"]
        self.assertEqual(ops[0], {"op": "mkdir", "path": "qbittorrent/Films", "name": "Film.2026.MULTi.VFQ.2160p.H265-SUPPLY"})
        self.assertIn(
            {"op": "move", "path": "qbittorrent/Films", "old_name": "Film.2026.MULTi.CA.mkv", "new_name": "Film.2026.MULTi.VFQ.2160p.H265-SUPPLY.mkv", "dest": "qbittorrent/Films/Film.2026.MULTi.VFQ.2160p.H265-SUPPLY"},
            ops,
        )
        self.assertIn(
            {"op": "move", "path": "qbittorrent/Films", "old_name": "Film.2026.MULTi.CA.srt", "new_name": "Film.2026.MULTi.VFQ.2160p.H265-SUPPLY.srt", "dest": "qbittorrent/Films/Film.2026.MULTi.VFQ.2160p.H265-SUPPLY"},
            ops,
        )
        self.assertFalse(any(op.get("op") == "rename" and op.get("old_name") == "Films" for op in ops))


    def _blue_prince_payload(self):
        """Real-world case: content at qbittorrent/Blue Prince, savePath points at Jeux."""
        release_dir = self.mount / "Qbittorrent" / "Blue Prince"
        release_dir.mkdir(parents=True, exist_ok=True)
        (release_dir / "BluePrince.exe").write_bytes(b"x" * 100)
        (release_dir / "data.bin").write_bytes(b"y" * 200)
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Blue Prince (v1.0.2.519)-ElAmigos",
                "state": "pausedDL",
                "category": "Jeux",
                "savePath": f"{self.QBIT_ROOT}/Jeux",
                "contentPath": f"{self.QBIT_ROOT}/Blue Prince",
            },
        ]
        app.state.qbit.files_payload = {
            VALID_HASH: [
                {"name": "BluePrince.exe", "size": 100},
                {"name": "data.bin", "size": 200},
            ]
        }
        relink_service._SCAN_CACHE["data"] = None
        relink_service._SCAN_CACHE["ts"] = 0.0
        return release_dir

    def test_blue_prince_plans_both_in_place_and_category_targets(self):
        self._blue_prince_payload()
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["relinkCount"], 1)
        self.assertEqual(plan["categoryRelinkCount"], 1)

        in_place_group = plan["relink"][0]
        self.assertEqual(in_place_group["location"], self.QBIT_ROOT)
        in_place_ops = in_place_group["entries"][0]["renames"]
        self.assertFalse(any(op.get("dest") == "Qbittorrent/Blue Prince/Blue Prince" for op in in_place_ops))
        self.assertIn(
            {"op": "rename", "path": "Qbittorrent", "old_name": "Blue Prince", "new_name": "Blue Prince (v1.0.2.519)-ElAmigos"},
            in_place_ops,
        )

        category_group = plan["relinkCategory"][0]
        self.assertEqual(category_group["location"], f"{self.QBIT_ROOT}/Jeux")
        category_entry = category_group["entries"][0]
        self.assertTrue(category_entry["categoryApplied"])
        self.assertIn(
            {"op": "mkdir", "path": "Qbittorrent", "name": "Jeux"},
            category_entry["renames"],
        )
        self.assertIn(
            {"op": "move", "path": "Qbittorrent", "old_name": "Blue Prince", "new_name": "Blue Prince (v1.0.2.519)-ElAmigos", "dest": "Qbittorrent/Jeux"},
            category_entry["renames"],
        )
        self.assertEqual(sum(op["op"] == "move" for op in category_entry["renames"]), 1)

    def test_blue_prince_keeps_clean_root_folder_name_from_torrent_manifest(self):
        self._blue_prince_payload()
        app.state.qbit.files_payload = {
            VALID_HASH: [
                {"name": "Blue Prince/BluePrince.exe", "size": 100},
                {"name": "Blue Prince/data.bin", "size": 200},
            ]
        }
        relink_service._SCAN_CACHE["data"] = None
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        category_entry = plan["relinkCategory"][0]["entries"][0]
        self.assertIn(
            {"op": "move", "path": "Qbittorrent", "old_name": "Blue Prince", "new_name": "", "dest": "Qbittorrent/Jeux"},
            category_entry["renames"],
        )
        in_place_ops = plan["relink"][0]["entries"][0]["renames"]
        self.assertFalse(any(op.get("op") == "rename" and op.get("old_name") == "Blue Prince" for op in in_place_ops))

    def test_nested_game_content_is_located_at_its_content_root(self):
        release_dir = self.mount / "Qbittorrent" / "Blue Prince"
        data_dir = release_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "game.exe").write_bytes(b"x" * 100)
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Blue Prince (v1.0.2.519)-ElAmigos",
                "state": "pausedDL",
                "category": "Jeux",
                "savePath": f"{self.QBIT_ROOT}/Jeux",
                "contentPath": f"{self.QBIT_ROOT}/Blue Prince",
            },
        ]
        app.state.qbit.files_payload = {
            VALID_HASH: [{"name": "Blue Prince/data/game.exe", "size": 100}]
        }
        relink_service._SCAN_CACHE["data"] = None
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["relink"][0]["entries"][0]["located"], "Qbittorrent/Blue Prince")
        self.assertFalse(any(
            op.get("op") == "rename" and op.get("old_name") == "data"
            for op in plan["relink"][0]["entries"][0]["renames"]
        ))
        category_entry = plan["relinkCategory"][0]["entries"][0]
        self.assertIn(
            {"op": "move", "path": "Qbittorrent", "old_name": "Blue Prince", "new_name": "", "dest": "Qbittorrent/Jeux"},
            category_entry["renames"],
        )

    def test_relink_job_accepts_category_mode_and_rejects_unknown_mode(self):
        request = mock.Mock(app=app)
        key, run = _runner(
            request,
            JobRequest(type="relink", params={"hashes": [VALID_HASH], "mode": "category", "recheck": True}),
        )
        self.assertTrue(key.startswith("relink:"))
        self.assertTrue(callable(run))
        with self.assertRaises(ValueError):
            _runner(
                request,
                JobRequest(type="relink", params={"hashes": [VALID_HASH], "mode": "move_everything"}),
            )

    def test_targeted_category_repair_uses_fast_locator_and_moves_folder_once(self):
        self._blue_prince_payload()
        recorded: list[dict[str, str]] = []

        async def fake_arrange_batch(items):
            recorded.extend(items)
            return {"results": [{"success": True} for _ in items], "failed": 0}

        with (
            mock.patch.object(relink_service, "_scan_index_async", new=mock.AsyncMock(side_effect=AssertionError("full scan"))),
            mock.patch("torrent_panel.services.cloud_panel.arrange_batch", side_effect=fake_arrange_batch),
        ):
            response = self.post_action(
                "/torrent-panel/api/torrents/relink",
                {"hashes": [VALID_HASH], "mode": "category", "recheck": True},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["relinked"], 1)
        self.assertEqual(body["result"]["mode"], "category")
        self.assertIn({"op": "mkdir", "path": "Qbittorrent", "name": "Jeux"}, recorded)
        self.assertIn(
            {"op": "move", "path": "Qbittorrent", "old_name": "Blue Prince", "new_name": "Blue Prince (v1.0.2.519)-ElAmigos", "dest": "Qbittorrent/Jeux"},
            recorded,
        )
        self.assertEqual(sum(op["op"] == "move" for op in recorded), 1)
        self.assertIn(("set_location", [VALID_HASH], f"{self.QBIT_ROOT}/Jeux"), app.state.qbit.calls)
        self.assertIn(("set_content_layout", [VALID_HASH], "Subfolder"), app.state.qbit.calls)
        self.assertIn(("recheck", [VALID_HASH]), app.state.qbit.calls)

    def test_category_repair_falls_back_to_in_place_on_destination_collision(self):
        self._blue_prince_payload()
        (self.mount / "Qbittorrent" / "Jeux" / "Blue Prince (v1.0.2.519)-ElAmigos").mkdir(parents=True)
        response = self.client.get("/torrent-panel/api/torrents/relink-preview")
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["categoryFallbackCount"], 1)
        group = plan["relinkCategory"][0]
        self.assertEqual(group["location"], self.QBIT_ROOT)
        entry = group["entries"][0]
        self.assertFalse(entry["categoryApplied"])
        self.assertEqual(entry["categoryReason"], "Collision de destination")


class OrganizerTests(BackendTests):
    QBIT_ROOT = "/home/micilico/downloads/qbittorrent"

    def _movie_payload(self):
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Dune.2021.1080p.MULTi",
                "state": "uploading",
                "progress": 1,
                "size": 10,
                "remaining": 0,
                "savePath": self.QBIT_ROOT,
            }
        ]
        app.state.qbit.files_payload = {VALID_HASH: [{"name": "Dune.2021.1080p.MULTi.mkv", "size": 10}]}

    def test_library_inventory_keeps_release_folders_directly_under_qbittorrent(self):
        mount = Path(tempfile.mkdtemp())
        release = mount / "qbittorrent" / "Euphoria.S03.MULTi.VFF.1080p.WEB.EAC3.5.1.H265-TFA" / "Saison 3"
        release.mkdir(parents=True)
        (release / "Euphoria.S03E01.mkv").write_bytes(b"episode")
        torrent_hash = "e" * 40
        app.state.qbit.torrents_payload = [{
            "hash": torrent_hash,
            "name": "Euphoria.S03.MULTi.VFF.1080p.WEB.EAC3.5.1.H265-TFA",
            "state": "uploading",
            "progress": 1,
            "size": 7,
            "remaining": 0,
            "savePath": self.QBIT_ROOT,
        }]
        app.state.qbit.files_payload = {torrent_hash: [{"name": "Euphoria.S03E01.mkv", "size": 7}]}

        inventory = organizer_service.build_library_inventory(str(mount), self.QBIT_ROOT)
        plan = asyncio.run(
            organizer_service.build_organization_plan(
                app.state.qbit,
                self.QBIT_ROOT,
                mount_root=str(mount),
                inventory=inventory,
            )
        )

        self.assertEqual(plan["warnings"], [])
        self.assertEqual([entry["name"] for entry in plan["entries"]], [
            "Euphoria.S03.MULTi.VFF.1080p.WEB.EAC3.5.1.H265-TFA",
        ])

    def test_inventory_cache_rebuilds_when_invalidated_during_scan(self):
        cache = organizer_service.LibraryInventoryCache()
        scan_started = threading.Event()
        release_scan = threading.Event()
        builds = []

        def fake_build(mount_root, qbit_root):
            builds.append(qbit_root)
            scan_started.set()
            release_scan.wait(2)
            return organizer_service.LibraryInventory(
                os.path.realpath(mount_root),
                qbit_root,
                [],
                f"generation-{len(builds)}",
            )

        async def scenario():
            with mock.patch.object(organizer_service, "build_library_inventory", side_effect=fake_build):
                task = asyncio.create_task(cache.get("/tmp", self.QBIT_ROOT))
                self.assertTrue(await asyncio.to_thread(scan_started.wait, 2))
                cache.invalidate()
                release_scan.set()
                return await task

        inventory = asyncio.run(scenario())

        self.assertEqual(builds, [self.QBIT_ROOT, self.QBIT_ROOT])
        self.assertEqual(inventory.generation, "generation-2")
        self.assertEqual(cache._inventory.generation, "generation-2")

    def test_inventory_cache_key_includes_qbittorrent_root(self):
        cache = organizer_service.LibraryInventoryCache()
        builds = []

        def fake_build(mount_root, qbit_root):
            builds.append(qbit_root)
            return organizer_service.LibraryInventory(
                os.path.realpath(mount_root),
                qbit_root,
                [],
                qbit_root,
            )

        with mock.patch.object(organizer_service, "build_library_inventory", side_effect=fake_build):
            first = asyncio.run(cache.get("/tmp", self.QBIT_ROOT))
            second = asyncio.run(cache.get("/tmp", "/other/downloads/media"))

        self.assertEqual(first.qbit_root, self.QBIT_ROOT)
        self.assertEqual(second.qbit_root, "/other/downloads/media")
        self.assertEqual(builds, [self.QBIT_ROOT, "/other/downloads/media"])

    def test_movie_preview_keeps_filename_and_builds_jellyfin_folder(self):
        self._movie_payload()
        with mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT):
            response = self.client.get("/torrent-panel/api/torrents/organize-preview")
        self.assertEqual(response.status_code, 200)
        entry = response.json()["plan"]["entries"][0]
        self.assertEqual(entry["folder"], "Dune (2021)")
        self.assertEqual(entry["targetPath"], f"{self.QBIT_ROOT}/Films")
        self.assertEqual(
            entry["operations"],
            [{"oldPath": "Dune.2021.1080p.MULTi.mkv", "newPath": "Dune (2021)/Dune.2021.1080p.MULTi.mkv"}],
        )

    def test_movie_identity_accepts_year_before_title_and_surrounding_release_noise(self):
        self.assertEqual(
            organizer_service._movie_identity(
                "1080p 2001 L'odyssée De L'espace - FR EN x264 ac3 mHDgz.mkv"
            ),
            ("L'odyssée De L'espace", "2001"),
        )
        self.assertEqual(
            organizer_service._movie_identity("Dune.2021.WEB-DL.1080p.FR.mkv"),
            ("Dune", "2021"),
        )
        self.assertEqual(
            organizer_service._movie_identity("2021 1080p Dune FR.mkv"),
            ("Dune", "2021"),
        )
        self.assertEqual(
            organizer_service._movie_identity("Dune 1080p 2021.mkv"),
            ("Dune", "2021"),
        )

    def test_movie_identity_prefers_the_release_year_over_a_year_in_the_title(self):
        self.assertEqual(
            organizer_service._movie_identity("Blade Runner 2049 2017 2160p HDR.mkv"),
            ("Blade Runner 2049", "2017"),
        )
        self.assertEqual(
            organizer_service._movie_identity("The French Connection 1971 1080p.mkv"),
            ("The French Connection", "1971"),
        )
        self.assertEqual(
            organizer_service._movie_identity("2001 A Space Odyssey 1968 2160p.mkv"),
            ("2001 A Space Odyssey", "1968"),
        )
        self.assertEqual(
            organizer_service._movie_identity("Dune.2021"),
            ("Dune", "2021"),
        )
        self.assertIsNone(
            organizer_service._movie_identity("1080p 2021 FR EN x264 ac3.mkv")
        )

    def test_weak_game_release_marker_never_overrides_a_video_payload(self):
        self.assertFalse(organizer_service._has_game_signature(
            "Movie.2021.PLAZA",
            ["Movie.2021.mkv", "Movie.2021.rar"],
        ))
        self.assertEqual(
            organizer_service._classify_torrent(
                {"name": "Movie.2021.PLAZA", "category": "prowlarr"},
                ["Movie.2021.mkv", "Movie.2021.rar"],
            ),
            ("film", "heuristic"),
        )
        self.assertTrue(organizer_service._has_game_signature(
            "Movie.2021.PLAZA",
            ["Movie.2021.mkv", "game.json"],
        ))

    def test_game_installer_with_executable_and_payload_is_detected(self):
        paths = [
            "House Flipper 2/HouseFlipper2.exe",
            "House Flipper 2/HouseFlipper2_Data/level0",
            "House Flipper 2/HouseFlipper2_Data/sharedassets0.assets",
            "House Flipper 2/data.bin",
            "House Flipper 2/UnityPlayer.dll",
        ]
        self.assertTrue(organizer_service._has_game_signature("House Flipper 2 - v1.12.0", paths))
        self.assertEqual(
            organizer_service._classify_torrent(
                {"name": "House Flipper 2 - v1.12.0", "category": ""},
                paths,
            ),
            ("game", "game_signature"),
        )
        # A plain Windows installer with game data is enough even without a
        # release group marker.
        self.assertTrue(organizer_service._has_game_signature(
            "Some Game",
            ["setup.exe", "setup-1.bin", "setup.dll"],
        ))
        # An executable plus a version token is accepted, but never an archive
        # alone (that stays ambiguous and needs the manual override).
        self.assertTrue(organizer_service._has_game_signature(
            "House Flipper 2 - v1.12.0",
            ["HouseFlipper2.exe"],
        ))
        self.assertFalse(organizer_service._has_game_signature(
            "House Flipper 2 - v1.12.0",
            ["House.Flipper.2.rar"],
        ))

    def test_game_installer_never_overrides_a_video_payload(self):
        self.assertFalse(organizer_service._has_game_signature(
            "Movie.2021.v2.1080p",
            ["Movie.2021.mkv", "setup.exe", "data.bin"],
        ))
        self.assertEqual(
            organizer_service._classify_torrent(
                {"name": "Movie.2021.v2.1080p", "category": "prowlarr"},
                ["Movie.2021.mkv", "setup.exe", "data.bin"],
            ),
            ("film", "heuristic"),
        )

    def test_explicit_game_category_allows_an_archive_only_release(self):
        self.assertEqual(
            organizer_service._classify_torrent(
                {"name": "House Flipper 2 - v1.12.0", "category": "Jeux"},
                ["House.Flipper.2.rar"],
            ),
            ("game", "explicit_category"),
        )

    def test_game_and_book_manifests_must_exist_without_shared_sidecar_false_positive(self):
        mount = Path(tempfile.mkdtemp())
        library = mount / "qbittorrent"
        (library / "Other Game").mkdir(parents=True)
        (library / "Other Game" / "readme.txt").write_bytes(b"read")
        game_hash = "c" * 40
        book_hash = "d" * 40
        app.state.qbit.torrents_payload = [
            {
                "hash": game_hash,
                "name": "Game.nsp",
                "category": "Jeux",
                "state": "pausedDL",
                "progress": 1,
                "size": 14,
                "remaining": 0,
                "savePath": self.QBIT_ROOT,
            },
            {
                "hash": book_hash,
                "name": "Dune.epub",
                "category": "Livres",
                "state": "pausedDL",
                "progress": 1,
                "size": 9,
                "remaining": 0,
                "savePath": self.QBIT_ROOT,
            },
        ]
        app.state.qbit.files_payload = {
            game_hash: [
                {"name": "Game.nsp", "size": 10},
                {"name": "readme.txt", "size": 4},
            ],
            book_hash: [{"name": "Dune.epub", "size": 9}],
        }
        inventory = organizer_service.build_library_inventory(str(mount), self.QBIT_ROOT)
        plan = asyncio.run(organizer_service.build_organization_plan(
            app.state.qbit,
            self.QBIT_ROOT,
            mount_root=str(mount),
            inventory=inventory,
        ))

        self.assertTrue(inventory.available)
        self.assertEqual(plan["entries"], [])
        missing = {item["hash"]: item for item in plan["missingTorrents"]}
        self.assertIn(game_hash, missing)
        self.assertIn(book_hash, missing)
        self.assertIn("manifeste", missing[game_hash]["reason"])

    def test_existing_foreign_target_with_same_name_and_size_blocks_plan(self):
        mount = Path(tempfile.mkdtemp())
        library = mount / "qbittorrent"
        target = library / "Films" / "Dune (2021)" / "Dune.2021.1080p.MULTi.mkv"
        target.parent.mkdir(parents=True)
        content = b"0123456789"
        target.write_bytes(content)
        (library / "Dune.2021.1080p.MULTi.mkv").write_bytes(content)
        self._movie_payload()
        inventory = organizer_service.build_library_inventory(str(mount), self.QBIT_ROOT)

        plan = asyncio.run(organizer_service.build_organization_plan(
            app.state.qbit,
            self.QBIT_ROOT,
            mount_root=str(mount),
            inventory=inventory,
        ))

        self.assertEqual(plan["entries"], [])
        self.assertTrue(any(
            "non associé au torrent" in warning["reason"]
            for warning in plan["warnings"]
        ))

    def _sidecar_collision_plan(self, mount: Path):
        library = mount / "qbittorrent"
        source = library / "Tiny.Bookshop.v1.1.6-TENOKE"
        source.mkdir(parents=True, exist_ok=True)
        (source / "TinyBookshop.exe").write_bytes(b"x" * 10)
        (source / "tenoke-tiny.bookshop.v1.1.6.nfo").write_bytes(b"info")
        app.state.qbit.torrents_payload = [{
            "hash": VALID_HASH,
            "name": "Tiny.Bookshop.v1.1.6-TENOKE",
            "state": "uploading",
            "progress": 1,
            "size": 14,
            "remaining": 0,
            "category": "",
            "savePath": self.QBIT_ROOT,
        }]
        app.state.qbit.files_payload = {VALID_HASH: [
            {"name": "Tiny.Bookshop.v1.1.6-TENOKE/TinyBookshop.exe", "size": 10},
            {"name": "Tiny.Bookshop.v1.1.6-TENOKE/tenoke-tiny.bookshop.v1.1.6.nfo", "size": 4},
        ]}
        inventory = organizer_service.build_library_inventory(str(mount), self.QBIT_ROOT)
        return asyncio.run(organizer_service.build_organization_plan(
            app.state.qbit,
            self.QBIT_ROOT,
            mount_root=str(mount),
            inventory=inventory,
        ))

    def test_sidecar_collision_is_trashed_instead_of_blocking_the_torrent(self):
        mount = Path(tempfile.mkdtemp())
        destination = mount / "qbittorrent" / "Jeux" / "Tiny.Bookshop.v1.1.6-TENOKE"
        destination.mkdir(parents=True)
        (destination / "tenoke-tiny.bookshop.v1.1.6.nfo").write_bytes(b"old!")

        plan = self._sidecar_collision_plan(mount)

        self.assertEqual(len(plan["entries"]), 1)
        entry = plan["entries"][0]
        self.assertEqual(entry["kind"], "game")
        self.assertEqual(entry["targetPath"], f"{self.QBIT_ROOT}/Jeux")
        self.assertIn(
            {
                "op": "delete",
                "path": "qbittorrent/Jeux/Tiny.Bookshop.v1.1.6-TENOKE",
                "old_name": "tenoke-tiny.bookshop.v1.1.6.nfo",
            },
            entry["cleanup"],
        )
        self.assertTrue(any("Conflit de métadonnées" in warning["reason"] for warning in plan["warnings"]))

        from torrent_panel.services import cloud_panel as cloud_panel_service

        cleanup_calls: list[list[dict[str, str]]] = []

        async def fake_arrange_batch(items):
            cleanup_calls.append(list(items))
            return {"results": [{"success": True} for _ in items], "failed": 0}

        with mock.patch.object(cloud_panel_service, "arrange_batch", side_effect=fake_arrange_batch):
            result = asyncio.run(organizer_service.apply_organization_plan(app.state.qbit, plan, recheck=False))

        self.assertEqual(result["organized"], 1)
        self.assertEqual(cleanup_calls, [entry["cleanup"]])
        self.assertIn(("set_location", [VALID_HASH], f"{self.QBIT_ROOT}/Jeux"), app.state.qbit.calls)

    def test_payload_collision_still_blocks_the_torrent(self):
        mount = Path(tempfile.mkdtemp())
        destination = mount / "qbittorrent" / "Jeux" / "Tiny.Bookshop.v1.1.6-TENOKE"
        destination.mkdir(parents=True)
        (destination / "TinyBookshop.exe").write_bytes(b"other")

        plan = self._sidecar_collision_plan(mount)

        self.assertEqual(plan["entries"], [])
        self.assertTrue(any(
            "Collision de destination" in warning["reason"]
            for warning in plan["warnings"]
        ))

    def test_preview_can_be_scoped_to_one_torrent(self):
        self._movie_payload()
        other_hash = "b" * 40
        other = dict(app.state.qbit.torrents_payload[0])
        other.update({"hash": other_hash, "name": "Arrival.2016.1080p"})
        app.state.qbit.torrents_payload.append(other)
        app.state.qbit.files_payload[other_hash] = [{"name": "Arrival.2016.1080p.mkv", "size": 10}]
        with mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT):
            response = self.client.get(
                "/torrent-panel/api/torrents/organize-preview",
                params={"hash": VALID_HASH},
            )
        self.assertEqual(response.status_code, 200)
        entries = response.json()["plan"]["entries"]
        self.assertEqual([entry["hash"] for entry in entries], [VALID_HASH])

    def test_series_preview_flattens_release_folder_but_keeps_episode_name(self):
        torrent_hash = "b" * 40
        app.state.qbit.torrents_payload = [
            {
                "hash": torrent_hash,
                "name": "The.Show.S01.1080p",
                "state": "stalledUP",
                "progress": 1,
                "size": 20,
                "remaining": 0,
                "savePath": self.QBIT_ROOT,
            }
        ]
        app.state.qbit.files_payload = {
            torrent_hash: [
                {"name": "The.Show.S01.1080p/The.Show.S01E01.mkv", "size": 10},
                {"name": "The.Show.S01.1080p/The.Show.S01E01.srt", "size": 1},
            ]
        }
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        entry = plan["entries"][0]
        self.assertEqual(entry["folder"], "The Show")
        self.assertEqual(entry["targetPath"], f"{self.QBIT_ROOT}/Series")
        self.assertEqual(
            entry["operations"],
            [{"type": "folder", "oldPath": "The.Show.S01.1080p", "newPath": "The Show/Saison 1"}],
        )

    def test_alt_episode_notation_is_classified_as_series(self):
        torrent_hash = "b" * 40
        app.state.qbit.torrents_payload = [{
            "hash": torrent_hash,
            "name": "The.Show.1x02.1080p",
            "state": "uploading",
            "progress": 1,
            "size": 10,
            "remaining": 0,
            "savePath": self.QBIT_ROOT,
        }]
        app.state.qbit.files_payload = {torrent_hash: [{"name": "The.Show.1x02.mkv", "size": 10}]}
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        self.assertEqual(plan["entries"][0]["kind"], "series")
        self.assertIn("Saison 1", plan["entries"][0]["targetFiles"][0])

    def test_library_preview_and_run_are_persisted_without_secrets(self):
        self._movie_payload()
        with mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT):
            preview = self.client.get("/torrent-panel/api/torrents/organize-library-preview")
        self.assertEqual(preview.status_code, 200)
        run_id = preview.json()["runId"]
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", "/path/that/does/not/exist"),
            mock.patch.object(app.state.media_automation, "refresh_rclone", new=mock.AsyncMock()),
            mock.patch.object(app.state.media_automation, "trigger_jellyfin_scan", new=mock.AsyncMock(return_value={"scope": "global"})),
        ):
            response = self.post_action("/torrent-panel/api/torrents/organize-library", {"runId": run_id, "hashes": [VALID_HASH]})
        self.assertEqual(response.status_code, 200)
        stored = self.client.get(f"/torrent-panel/api/torrents/organize-library-runs/{run_id}").json()
        self.assertEqual(stored["status"], "completed")
        self.assertNotIn("QBITTORRENT_PASSWORD", str(stored))
        self.assertNotIn("JELLYFIN_API_KEY", str(stored))
        self.assertNotIn("resume", [call[0] for call in app.state.qbit.calls])

    def test_library_apply_skips_recheck_when_disabled(self):
        self._movie_payload()
        with mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT):
            preview = self.client.get("/torrent-panel/api/torrents/organize-library-preview")
        self.assertEqual(preview.status_code, 200)
        run_id = preview.json()["runId"]
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", "/path/that/does/not/exist"),
            mock.patch.object(torrent_routes, "ORGANIZER_RECHECK_ENABLED", False),
            mock.patch.object(app.state.media_automation, "refresh_rclone", new=mock.AsyncMock()),
            mock.patch.object(app.state.media_automation, "trigger_jellyfin_scan", new=mock.AsyncMock(return_value={"scope": "global"})),
        ):
            response = self.post_action("/torrent-panel/api/torrents/organize-library", {"runId": run_id, "hashes": [VALID_HASH]})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("recheck", [call[0] for call in app.state.qbit.calls])

    def test_stored_plan_rejects_new_qbittorrent_owner_before_any_mutation(self):
        mount = Path(tempfile.mkdtemp())
        library = mount / "qbittorrent"
        library.mkdir(parents=True)
        (library / "Dune.2021.1080p.MULTi.mkv").write_bytes(b"0123456789")
        self._movie_payload()
        cache = organizer_service.LibraryInventoryCache()
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", str(mount)),
            mock.patch.object(app.state, "library_inventory", cache),
        ):
            preview = self.client.get("/torrent-panel/api/torrents/organize-library-preview", params={"hash": VALID_HASH})
        self.assertEqual(preview.status_code, 200)
        run_id = preview.json()["runId"]

        conflicting_hash = "b" * 40
        app.state.qbit.torrents_payload.append({
            "hash": conflicting_hash,
            "name": "Dune.2021.2160p.MULTi",
            "state": "uploading",
            "progress": 1,
            "size": 10,
            "remaining": 0,
            "savePath": self.QBIT_ROOT,
        })
        app.state.qbit.files_payload[conflicting_hash] = [
            {"name": "Dune.2021.1080p.MULTi.mkv", "size": 10},
        ]
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", str(mount)),
            mock.patch.object(app.state, "library_inventory", cache),
        ):
            response = self.post_action(
                "/torrent-panel/api/torrents/organize-library",
                {"runId": run_id, "hashes": [VALID_HASH]},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "organization_plan_stale")
        mutation_calls = {"pause", "resume", "rename_file", "rename_folder", "set_location", "recheck"}
        self.assertFalse(any(call[0] in mutation_calls for call in app.state.qbit.calls))

    def test_stored_plan_rejects_inventory_drift_before_any_mutation(self):
        mount = Path(tempfile.mkdtemp())
        library = mount / "qbittorrent"
        library.mkdir(parents=True)
        (library / "Dune.2021.1080p.MULTi.mkv").write_bytes(b"0123456789")
        self._movie_payload()
        cache = organizer_service.LibraryInventoryCache()
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", str(mount)),
            mock.patch.object(app.state, "library_inventory", cache),
        ):
            preview = self.client.get("/torrent-panel/api/torrents/organize-library-preview", params={"hash": VALID_HASH})
        self.assertEqual(preview.status_code, 200)
        run_id = preview.json()["runId"]

        foreign_target = library / "Films" / "Dune (2021)" / "Dune.2021.1080p.MULTi.mkv"
        foreign_target.parent.mkdir(parents=True)
        foreign_target.write_bytes(b"foreign-data")
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", str(mount)),
            mock.patch.object(app.state, "library_inventory", cache),
        ):
            response = self.post_action(
                "/torrent-panel/api/torrents/organize-library",
                {"runId": run_id, "hashes": [VALID_HASH]},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "organization_plan_stale")
        mutation_calls = {"pause", "resume", "rename_file", "rename_folder", "set_location", "recheck"}
        self.assertFalse(any(call[0] in mutation_calls for call in app.state.qbit.calls))

    def test_unrelated_download_progress_does_not_invalidate_selected_plan(self):
        mount = Path(tempfile.mkdtemp())
        library = mount / "qbittorrent"
        library.mkdir(parents=True)
        (library / "Dune.2021.1080p.MULTi.mkv").write_bytes(b"0123456789")
        self._movie_payload()
        unrelated_hash = "b" * 40
        app.state.qbit.torrents_payload.append({
            "hash": unrelated_hash,
            "name": "Another.Download",
            "state": "downloading",
            "progress": 0.2,
            "size": 20,
            "remaining": 16,
            "savePath": self.QBIT_ROOT,
        })
        app.state.qbit.files_payload[unrelated_hash] = [
            {"name": "Another.Download.part", "size": 20},
        ]
        cache = organizer_service.LibraryInventoryCache()
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", str(mount)),
            mock.patch.object(app.state, "library_inventory", cache),
        ):
            preview = self.client.get("/torrent-panel/api/torrents/organize-library-preview", params={"hash": VALID_HASH})
        self.assertEqual(preview.status_code, 200)
        run_id = preview.json()["runId"]

        app.state.qbit.torrents_payload[1].update({"progress": 0.8, "remaining": 4})
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", str(mount)),
            mock.patch.object(app.state, "library_inventory", cache),
            mock.patch.object(app.state.media_automation, "refresh_rclone", new=mock.AsyncMock()),
            mock.patch.object(app.state.media_automation, "trigger_jellyfin_scan", new=mock.AsyncMock(return_value={})),
        ):
            response = self.post_action(
                "/torrent-panel/api/torrents/organize-library",
                {"runId": run_id, "hashes": [VALID_HASH]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["organized"], 1)

    def test_selected_torrent_becoming_incomplete_invalidates_plan_without_mutation(self):
        mount = Path(tempfile.mkdtemp())
        library = mount / "qbittorrent"
        library.mkdir(parents=True)
        (library / "Dune.2021.1080p.MULTi.mkv").write_bytes(b"0123456789")
        self._movie_payload()
        cache = organizer_service.LibraryInventoryCache()
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", str(mount)),
            mock.patch.object(app.state, "library_inventory", cache),
        ):
            preview = self.client.get("/torrent-panel/api/torrents/organize-library-preview", params={"hash": VALID_HASH})
        self.assertEqual(preview.status_code, 200)
        run_id = preview.json()["runId"]

        app.state.qbit.torrents_payload[0].update({
            "state": "downloading",
            "progress": 0.5,
            "remaining": 5,
        })
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", str(mount)),
            mock.patch.object(app.state, "library_inventory", cache),
        ):
            response = self.post_action(
                "/torrent-panel/api/torrents/organize-library",
                {"runId": run_id, "hashes": [VALID_HASH]},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "organization_plan_stale")
        mutation_calls = {"pause", "resume", "rename_file", "rename_folder", "set_location", "recheck"}
        self.assertFalse(any(call[0] in mutation_calls for call in app.state.qbit.calls))

    def test_library_preview_uses_backend_metadata_confidence(self):
        self._movie_payload()
        app.state.metadata_resolver.resolve = mock.AsyncMock(return_value={"status": "resolved", "confidence": "certain", "source": "jellyfin"})
        with mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT):
            response = self.client.get("/torrent-panel/api/torrents/organize-library-preview")
        self.assertEqual(response.status_code, 200)
        entry = response.json()["plan"]["entries"][0]
        self.assertEqual(entry["confidence"], "certain")
        self.assertEqual(entry["metadata"]["source"], "jellyfin")

    def test_ambiguous_backend_metadata_is_blocked(self):
        self._movie_payload()
        app.state.metadata_resolver.resolve = mock.AsyncMock(return_value={"status": "ambiguous", "confidence": "ambiguous"})
        with mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT):
            response = self.client.get("/torrent-panel/api/torrents/organize-library-preview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan"]["entries"], [])
        self.assertIn("ambiguë", response.json()["plan"]["warnings"][0]["reason"])

    def test_orphan_operations_use_cloud_relative_paths_and_move_only(self):
        operations = organizer_service.orphan_operations(
            {"path": "Incoming/Dune.2021.1080p.mkv"},
            self.QBIT_ROOT,
        )
        self.assertIsNotNone(operations)
        self.assertTrue(all(not str(item.get("path", "")).startswith("/") for item in operations or []))
        self.assertTrue(all(item.get("op") != "copy" for item in operations or []))
        self.assertEqual((operations or [])[-1]["op"], "move")
        self.assertTrue((operations or [])[-1]["dest"].startswith("qbittorrent/Films/"))

    def test_multi_season_pack_keeps_one_series_tree_and_each_season(self):
        torrent_hash = "c" * 40
        app.state.qbit.torrents_payload = [
            {
                "hash": torrent_hash,
                "name": "The.Show.Complete.1080p",
                "state": "uploading",
                "progress": 1,
                "size": 20,
                "remaining": 0,
                "savePath": self.QBIT_ROOT,
            }
        ]
        app.state.qbit.files_payload = {
            torrent_hash: [
                {"name": "The.Show.Complete.1080p/Season 1/The.Show.S01E01.mkv", "size": 10},
                {"name": "The.Show.Complete.1080p/Season 2/The.Show.S02E01.mkv", "size": 10},
            ]
        }
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        targets = {item["newPath"] for item in plan["entries"][0]["operations"]}
        self.assertEqual(
            targets,
            {
                "The Show/Saison 1/The.Show.S01E01.mkv",
                "The Show/Saison 2/The.Show.S02E01.mkv",
            },
        )

    def test_destination_collision_warns_and_blocks_both_torrents(self):
        duplicate_hash = "d" * 40
        self._movie_payload()
        duplicate = dict(app.state.qbit.torrents_payload[0])
        duplicate.update({"hash": duplicate_hash, "name": "Dune.2021.2160p.MULTi"})
        app.state.qbit.torrents_payload.append(duplicate)
        app.state.qbit.files_payload[duplicate_hash] = [{"name": "Dune.2021.1080p.MULTi.mkv", "size": 10}]
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        self.assertEqual(plan["entries"], [])
        self.assertTrue(any("Collision" in warning["reason"] for warning in plan["warnings"]))

    def test_incomplete_torrent_is_warning_and_never_planned(self):
        self._movie_payload()
        app.state.qbit.torrents_payload[0].update({"progress": 0.5, "remaining": 5})
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        self.assertEqual(plan["entries"], [])
        self.assertIn("incomplet", plan["warnings"][0]["reason"])
        self.assertIn("50,00 %", plan["warnings"][0]["reason"])
        self.assertIn("état uploading", plan["warnings"][0]["reason"])

    def test_complete_state_accepts_float_progress_rounding_noise(self):
        self.assertTrue(organizer_service._is_complete({
            "state": "pausedUP",
            "progress": 0.9999,
            "remaining": 12,
            "size": 1000,
        }))
        self.assertFalse(organizer_service._is_complete({
            "state": "pausedDL",
            "progress": 0.999,
            "remaining": 12,
            "size": 1000,
        }))

    def test_incomplete_warning_reports_precise_progress_and_remaining(self):
        detail = organizer_service._completion_detail({
            "state": "pausedDL",
            "progress": 0.9996,
            "remaining": 12 * 1024 * 1024,
            "size": 100,
        })
        self.assertIn("99,96 %", detail)
        self.assertIn("12,0 Mo restants", detail)
        self.assertIn("pausedDL", detail)

    def test_category_path_configuration_is_normalized_to_qbittorrent_root(self):
        self._movie_payload()
        plan = asyncio.run(
            organizer_service.build_organization_plan(app.state.qbit, f"{self.QBIT_ROOT}/Series")
        )
        self.assertEqual(plan["entries"][0]["targetPath"], f"{self.QBIT_ROOT}/Films")

    def test_incompatible_qbittorrent_version_is_rejected_before_mutation(self):
        self._movie_payload()
        app.state.qbit.webapi_version = mock.AsyncMock(return_value="2.7.0")
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        self.assertEqual(plan["entries"], [])
        self.assertIn("2.8 minimum", plan["warnings"][0]["reason"])
        self.assertEqual(app.state.qbit.calls, [])

    def test_apply_uses_qbittorrent_then_rechecks_and_never_resumes(self):
        self._movie_payload()
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        result = asyncio.run(
            organizer_service.apply_organization_plan(app.state.qbit, plan, app.state.verified_resume, recheck=True)
        )
        self.assertEqual(result["organized"], 1)
        calls = app.state.qbit.calls
        pause_index = calls.index(("pause", [VALID_HASH]))
        location_index = calls.index(("set_location", [VALID_HASH], f"{self.QBIT_ROOT}/Films"))
        recheck_index = calls.index(("recheck", [VALID_HASH]))
        rename_calls = [index for index, call in enumerate(calls) if call[0] == "rename_file"]
        self.assertLess(pause_index, rename_calls[0])
        self.assertLess(rename_calls[-1], location_index)
        self.assertLess(location_index, recheck_index)
        self.assertEqual(app.state.verified_resume.snapshot()["pending"], 0)
        self.assertNotIn("resume", [call[0] for call in calls])

    def test_apply_skips_recheck_by_default_to_spare_disk_io(self):
        self._movie_payload()
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        result = asyncio.run(
            organizer_service.apply_organization_plan(app.state.qbit, plan, None)
        )
        self.assertEqual(result["organized"], 1)
        self.assertNotIn("recheck", [call[0] for call in app.state.qbit.calls])
        self.assertNotIn("resume", [call[0] for call in app.state.qbit.calls])

    def test_failed_rename_rolls_back_and_never_rechecks_or_resumes(self):
        self._movie_payload()
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        direct_path = plan["entries"][0]["operations"][0]["oldPath"]
        app.state.qbit.fail_rename_old_paths = {direct_path}
        result = asyncio.run(organizer_service.apply_organization_plan(app.state.qbit, plan, app.state.verified_resume))
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["results"][0]["rollback"], "ok")
        self.assertNotIn("recheck", [call[0] for call in app.state.qbit.calls])
        self.assertEqual(app.state.verified_resume.snapshot()["pending"], 0)

    def test_single_file_movie_uses_one_direct_rename_without_hidden_staging(self):
        self._movie_payload()
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        entry = plan["entries"][0]
        self.assertEqual(
            entry["operations"],
            [{"oldPath": "Dune.2021.1080p.MULTi.mkv", "newPath": "Dune (2021)/Dune.2021.1080p.MULTi.mkv"}],
        )
        result = asyncio.run(organizer_service.apply_organization_plan(app.state.qbit, plan, app.state.verified_resume))
        self.assertEqual(result["organized"], 1)
        rename_calls = [call for call in app.state.qbit.calls if call[0] == "rename_file"]
        self.assertEqual(
            rename_calls,
            [("rename_file", VALID_HASH, "Dune.2021.1080p.MULTi.mkv", "Dune (2021)/Dune.2021.1080p.MULTi.mkv")],
        )
        self.assertFalse(any(".__dashboard_organize__" in str(call) for call in rename_calls))

    def test_temporary_path_stays_in_the_existing_parent_directory(self):
        self.assertEqual(
            organizer_service._temporary_path(VALID_HASH, 0, "Films/Dune (2021)/Dune.mkv"),
            "Films/Dune (2021)/.dashboard-organize-aaaaaaaaaaaa-00000.mkv",
        )
        self.assertEqual(
            organizer_service._temporary_path(VALID_HASH, 1, "Dune.mkv"),
            ".dashboard-organize-aaaaaaaaaaaa-00001.mkv",
        )

    def test_collision_staging_keeps_temporary_paths_next_to_their_files(self):
        app.state.qbit.torrents_payload = [
            {
                "hash": VALID_HASH,
                "name": "Swap",
                "state": "pausedDL",
                "progress": 1,
                "size": 2,
                "remaining": 0,
                "savePath": self.QBIT_ROOT,
            }
        ]
        app.state.qbit.files_payload = {
            VALID_HASH: [{"name": "A.mkv", "size": 1}, {"name": "B.mkv", "size": 1}],
        }
        plan = {
            "entries": [
                {
                    "hash": VALID_HASH,
                    "name": "Swap",
                    "currentPath": self.QBIT_ROOT,
                    "targetPath": self.QBIT_ROOT,
                    "operations": [
                        {"oldPath": "A.mkv", "newPath": "B.mkv"},
                        {"oldPath": "B.mkv", "newPath": "A.mkv"},
                    ],
                }
            ],
            "warnings": [],
        }
        result = asyncio.run(organizer_service.apply_organization_plan(app.state.qbit, plan, app.state.verified_resume))
        self.assertEqual(result["organized"], 1)
        rename_calls = [call for call in app.state.qbit.calls if call[0] == "rename_file"]
        self.assertEqual(
            [call[3] for call in rename_calls],
            [
                organizer_service._temporary_path(VALID_HASH, 0, "A.mkv"),
                organizer_service._temporary_path(VALID_HASH, 1, "B.mkv"),
                "B.mkv",
                "A.mkv",
            ],
        )
        self.assertFalse(any(".__dashboard_organize__" in str(call[3]) for call in rename_calls))

    def test_wait_for_rename_tracks_qbittorrent_metadata_catch_up(self):
        class SlowQbit:
            def __init__(self):
                self.snapshots = [
                    [{"name": "A.mkv", "size": 1}],
                    [{"name": "B.mkv", "size": 1}],
                ]
                self.calls = 0

            async def files(self, torrent_hash):
                self.calls += 1
                return list(self.snapshots[min(self.calls - 1, len(self.snapshots) - 1)])

        qbit = SlowQbit()
        asyncio.run(
            organizer_service._wait_for_rename(
                qbit,
                VALID_HASH,
                "file",
                "A.mkv",
                "B.mkv",
                timeout_seconds=1.0,
            )
        )
        self.assertGreaterEqual(qbit.calls, 2)

    def test_rename_metadata_timeout_rolls_back_and_keeps_torrent_paused(self):
        class LagOnceQbit(FakeQbit):
            def __init__(self):
                super().__init__()
                self.metadata_lag_remaining = 3
                self.stale_snapshot = None

            async def rename_file(self, torrent_hash, old_path, new_path):
                snapshot = [dict(item) for item in self.files_payload.get(torrent_hash, [])]
                await super().rename_file(torrent_hash, old_path, new_path)
                if self.metadata_lag_remaining:
                    self.stale_snapshot = snapshot
                    self.metadata_lag_remaining -= 1

            async def files(self, torrent_hash):
                if self.stale_snapshot is not None:
                    snapshot = self.stale_snapshot
                    if self.metadata_lag_remaining:
                        self.metadata_lag_remaining -= 1
                    else:
                        self.stale_snapshot = None
                    return snapshot
                return await super().files(torrent_hash)

        self._movie_payload()
        plan = asyncio.run(organizer_service.build_organization_plan(app.state.qbit, self.QBIT_ROOT))
        app.state.qbit = LagOnceQbit()
        app.state.qbit.torrents_payload = [{
            "hash": VALID_HASH,
            "name": "Dune.2021.1080p.MULTi",
            "state": "uploading",
            "progress": 1,
            "size": 10,
            "remaining": 0,
            "savePath": self.QBIT_ROOT,
        }]
        app.state.qbit.files_payload = {
            VALID_HASH: [{"name": "Dune.2021.1080p.MULTi.mkv", "size": 10}],
        }
        real_wait = organizer_service._wait_for_rename
        wait_count = 0

        async def short_first_wait(*args, **kwargs):
            nonlocal wait_count
            wait_count += 1
            if wait_count == 1:
                kwargs["timeout_seconds"] = 0.01
            return await real_wait(*args, **kwargs)

        with mock.patch.object(organizer_service, "_wait_for_rename", side_effect=short_first_wait):
            result = asyncio.run(organizer_service.apply_organization_plan(app.state.qbit, plan))

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["results"][0]["rollback"], "ok")
        self.assertIn("confirmé", result["results"][0]["error"])
        self.assertEqual(app.state.qbit.torrents_payload[0]["state"], "stoppedUP")
        self.assertEqual(
            app.state.qbit.files_payload[VALID_HASH][0]["name"],
            "Dune.2021.1080p.MULTi.mkv",
        )
        self.assertNotIn("recheck", [call[0] for call in app.state.qbit.calls])
        self.assertNotIn("resume", [call[0] for call in app.state.qbit.calls])
    def test_resume_manager_never_resumes_automatically(self):
        self._movie_payload()
        manager = app.state.verified_resume
        manager.track(VALID_HASH)
        app.state.qbit.torrents_payload[0]["progress"] = 0.99
        asyncio.run(manager.check_once())
        self.assertNotIn("resume", [call[0] for call in app.state.qbit.calls])
        app.state.qbit.torrents_payload[0]["progress"] = 1
        asyncio.run(manager.check_once())
        self.assertNotIn("resume", [call[0] for call in app.state.qbit.calls])
        self.assertEqual(manager.snapshot()["pending"], 0)

    def test_organize_requires_csrf(self):
        self._movie_payload()
        response = self.client.post("/torrent-panel/api/torrents/organize", json={})
        self.assertEqual(response.status_code, 403)

    def test_organize_endpoint_refreshes_rclone_and_jellyfin_after_success(self):
        self._movie_payload()
        with (
            mock.patch.object(torrent_routes, "QBIT_SAVE_PATH", self.QBIT_ROOT),
            mock.patch.object(torrent_routes, "MEDIA_MOUNT_PATH", "/path/that/does/not/exist"),
            mock.patch.object(app.state.media_automation, "refresh_rclone", new=mock.AsyncMock()) as refresh,
            mock.patch.object(app.state.media_automation, "trigger_jellyfin_scan", new=mock.AsyncMock(return_value={"scope": "global"})) as scan,
        ):
            response = self.post_action("/torrent-panel/api/torrents/organize", {})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["organized"], 1)
        self.assertEqual(body["refresh"], {"rclone": "requested", "jellyfin": "requested"})
        refresh.assert_awaited_once_with(
            dirs=["qbittorrent/Films", "qbittorrent/Series"],
            recursive=True,
            async_run=True,
        )
        scan.assert_awaited_once_with([])


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

    async def test_ensure_category_creates_only_missing_categories(self):
        self.client = QBittorrentClient(QbitConfig(url="http://127.0.0.1:1", username="u", password="p"))
        calls = []

        async def fake_request(method, path, *, data=None, **kwargs):
            calls.append((method, path, data))
            if path.endswith("/categories"):
                return FakeResponse({"Films": {"savePath": "/Qbittorrent/Films"}})
            return FakeResponse()

        self.client._request = fake_request
        await self.client.ensure_category("Films", "/Qbittorrent/Films")
        self.assertFalse(any("createCategory" in call[1] for call in calls))
        await self.client.ensure_category("Jeux", "/Qbittorrent/Jeux")
        self.assertIn(
            ("POST", "/api/v2/torrents/createCategory", {"category": "Jeux", "savePath": "/Qbittorrent/Jeux"}),
            calls,
        )

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

    async def test_sync_maindata_returns_the_same_mapping_as_the_list_endpoint(self):
        raw = {
            "hash": VALID_HASH,
            "name": "Example",
            "state": "downloading",
            "progress": 0.42,
            "dlspeed": 2048,
            "upspeed": 512,
            "ratio": 0.5,
            "size": 4096,
            "downloaded": 2048,
            "uploaded": 512,
            "amount_left": 2048,
            "eta": 120,
            "added_on": 20,
            "completion_on": 0,
            "num_seeds": 3,
            "num_leeches": 1,
            "availability": 2.5,
            "category": "Series",
            "tags": "auto",
            "save_path": "/downloads/Series",
            "content_path": "/downloads/Series/Example",
            "tracker": "https://user:secret@tracker.test/announce?passkey=hidden",
            "priority": 2,
            "last_activity": 1700000000,
            "dl_limit": 1024,
            "up_limit": -1,
            "seq_dl": True,
            "is_private": True,
        }

        incremental = QBittorrentClient(
            QbitConfig(url="http://127.0.0.1:1", username="u", password="p", torrents_cache_ttl_seconds=0)
        )
        listed = QBittorrentClient(
            QbitConfig(url="http://127.0.0.1:1", username="u", password="p", torrents_cache_ttl_seconds=0)
        )
        self.addAsyncCleanup(incremental.close)
        self.addAsyncCleanup(listed.close)

        async def sync_request(method, path, *, params=None, **kwargs):
            self.assertEqual(path, "/api/v2/sync/maindata")
            payload = dict(raw)
            payload.pop("hash", None)
            return FakeResponse({"rid": 4, "full_update": True, "torrents": {VALID_HASH: payload}})

        async def list_request(method, path, *, params=None, **kwargs):
            if path.endswith("/sync/maindata"):
                raise QbitError(404, "Torrent inconnu ou action qBittorrent indisponible.", code="qbit_action_unavailable")
            self.assertEqual(path, "/api/v2/torrents/info")
            return FakeResponse([raw])

        incremental._request = sync_request
        listed._request = list_request

        from_sync = await incremental.torrents()
        from_list = await listed.torrents()

        self.assertEqual(from_sync, from_list)
        self.assertEqual(from_sync[0]["tracker"], "tracker.test")
        self.assertNotIn("secret", str(from_sync))
        self.assertNotIn("passkey", str(from_sync))

    async def test_sync_maindata_applies_incremental_updates_and_removals(self):
        removed_hash = "b" * 40
        seen_params = []

        async def fake_request(method, path, *, params=None, **kwargs):
            self.assertEqual(path, "/api/v2/sync/maindata")
            seen_params.append(params)
            if params:
                return FakeResponse(
                    {
                        "rid": 11,
                        "full_update": False,
                        "torrents": {VALID_HASH: {"dlspeed": 4096}},
                        "torrents_removed": [removed_hash],
                    }
                )
            return FakeResponse(
                {
                    "rid": 10,
                    "full_update": True,
                    "torrents": {
                        VALID_HASH: {"name": "Example", "progress": 0.1, "dlspeed": 1024},
                        removed_hash: {"name": "Obsolete"},
                    },
                }
            )

        self.client = QBittorrentClient(
            QbitConfig(url="http://127.0.0.1:1", username="u", password="p", torrents_cache_ttl_seconds=0)
        )
        self.client._request = fake_request

        initial = await self.client.torrents()
        self.assertEqual([item["name"] for item in initial], ["Example", "Obsolete"])

        updated = await self.client.torrents()
        self.assertEqual([item["name"] for item in updated], ["Example"])
        self.assertEqual(updated[0]["progress"], 0.1)
        self.assertEqual(updated[0]["downloadSpeed"], 4096)
        self.assertEqual(updated[0]["name"], "Example")
        self.assertEqual(updated[0]["hash"], VALID_HASH)
        self.assertEqual(seen_params, [None, {"rid": 10}])

    async def test_sync_maindata_falls_back_to_the_full_list_when_unsupported(self):
        paths = []

        async def fake_request(method, path, *, params=None, **kwargs):
            paths.append(path)
            if path.endswith("/sync/maindata"):
                raise QbitError(404, "Torrent inconnu ou action qBittorrent indisponible.", code="qbit_action_unavailable")
            return FakeResponse([{"hash": VALID_HASH, "name": "Example"}])

        self.client = QBittorrentClient(
            QbitConfig(url="http://127.0.0.1:1", username="u", password="p", torrents_cache_ttl_seconds=0)
        )
        self.client._request = fake_request

        self.assertEqual([item["name"] for item in await self.client.torrents()], ["Example"])
        self.assertEqual([item["name"] for item in await self.client.torrents()], ["Example"])
        self.assertEqual(paths, ["/api/v2/sync/maindata", "/api/v2/torrents/info", "/api/v2/torrents/info"])

    async def test_sync_maindata_rejects_a_delta_it_cannot_anchor(self):
        paths = []

        async def fake_request(method, path, *, params=None, **kwargs):
            paths.append(path)
            if path.endswith("/sync/maindata"):
                return FakeResponse(
                    {"rid": 20, "full_update": False, "torrents": {VALID_HASH: {"progress": 0.5}}}
                )
            return FakeResponse(
                [
                    {"hash": VALID_HASH, "name": "Example"},
                    {"hash": "b" * 40, "name": "Second"},
                ]
            )

        self.client = QBittorrentClient(
            QbitConfig(url="http://127.0.0.1:1", username="u", password="p", torrents_cache_ttl_seconds=0)
        )
        self.client._request = fake_request

        first = await self.client.torrents()
        second = await self.client.torrents()

        # A delta the client cannot anchor must never hide torrents that only
        # the complete list knows about.
        self.assertEqual([item["name"] for item in first], ["Example", "Second"])
        self.assertEqual([item["name"] for item in second], ["Example", "Second"])
        self.assertEqual(
            paths,
            ["/api/v2/sync/maindata", "/api/v2/torrents/info", "/api/v2/sync/maindata", "/api/v2/torrents/info"],
        )

    async def test_concurrent_torrent_reads_share_one_upstream_request(self):
        calls = 0
        started = asyncio.Event()
        gate = asyncio.Event()

        async def fake_request(method, path, *, params=None, **kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await gate.wait()
            return FakeResponse({"rid": 31, "full_update": True, "torrents": {VALID_HASH: {"name": "Example"}}})

        self.client = QBittorrentClient(
            QbitConfig(url="http://127.0.0.1:1", username="u", password="p", torrents_cache_ttl_seconds=0)
        )
        self.client._request = fake_request

        readers = [asyncio.create_task(self.client.torrents()) for _ in range(3)]
        await started.wait()
        await asyncio.sleep(0.05)
        gate.set()
        results = await asyncio.gather(*readers)

        self.assertEqual(calls, 1)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0]["name"], "Example")

    async def test_rename_file_uses_metadata_aware_endpoint(self):
        self.client = QBittorrentClient(QbitConfig(url="http://127.0.0.1:1", username="u", password="p"))
        calls = []

        async def fake_request(method, path, *, data=None, **kwargs):
            calls.append((method, path, data))
            return FakeResponse()

        self.client._request = fake_request
        await self.client.rename_file(VALID_HASH, "old/file.mkv", "Dune (2021)/file.mkv")
        self.assertEqual(calls[-1][1], "/api/v2/torrents/renameFile")
        self.assertEqual(
            calls[-1][2],
            {"hash": VALID_HASH, "oldPath": "old/file.mkv", "newPath": "Dune (2021)/file.mkv"},
        )


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


class StorageSnapshotTests(unittest.IsolatedAsyncioTestCase):

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

    async def test_storage_snapshot_disables_quota_without_external_request(self):
        with self._patch_rclone():
            result = await storage_snapshot(mock.Mock())
        self.assertEqual(result["disk"]["source"], "disabled")
        self.assertFalse(result["disk"]["available"])
        self.assertEqual(result["disk"]["totalBytes"], 0)
        self.assertEqual(result["disk"]["usedBytes"], 0)

    def test_disk_unavailable_payload(self):
        payload = _disk_unavailable()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["source"], "disabled")
        self.assertEqual(payload["totalBytes"], 0)


if __name__ == "__main__":
    unittest.main()
