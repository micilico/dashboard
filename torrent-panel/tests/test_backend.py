import asyncio
import tempfile
import sys
import unittest
from pathlib import Path

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

    async def torrents(self):
        return list(self.torrents_payload)

    async def pause_many(self, hashes):
        self.calls.append(("pause", hashes))

    async def resume_many(self, hashes):
        self.calls.append(("resume", hashes))

    async def set_force_start_many(self, hashes, enabled):
        self.calls.append(("force_start", hashes, enabled))

    async def delete_many(self, hashes, delete_files):
        self.calls.append(("delete", hashes, delete_files))

    async def add_magnet(self, magnet, **kwargs):
        self.calls.append(("add", magnet, kwargs))

    async def ready(self):
        return True

    async def close(self):
        return None


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.original_qbit = app.state.qbit
        self.original_media = app.state.media_automation
        self.original_limiter = app.state.action_limiter
        self.original_csrf_tokens = dict(app.state.csrf_tokens)
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
        app.state.action_limiter = RateLimiter(max_calls=100, period_seconds=60, max_keys=100)
        app.state.csrf_tokens = {}
        self.client = TestClient(app)
        session = self.client.get("/torrent-panel/api/session").json()
        self.csrf = session["csrfToken"]

    def tearDown(self):
        self.client.close()
        app.state.qbit = self.original_qbit
        app.state.media_automation = self.original_media
        app.state.action_limiter = self.original_limiter
        app.state.csrf_tokens = self.original_csrf_tokens

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
                        "tracker": "https://tracker.test",
                        "priority": 1,
                    }
                ]
            )

        self.client._request = fake_request
        torrents = await self.client.torrents()
        self.assertEqual(torrents[0]["remaining"], 500)
        self.assertEqual(torrents[0]["eta"], 3600)
        self.assertEqual(torrents[0]["category"], "Films")
        self.assertEqual(torrents[0]["tracker"], "https://tracker.test")

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


class MediaAutomationTests(unittest.IsolatedAsyncioTestCase):
    def build_manager(self):
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
                mount_path=str(temp_dir),
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
        _qbit, manager = self.build_manager()
        calls = []

        async def fake_refresh():
            calls.append("rclone")

        async def fake_mount():
            calls.append("mount")

        async def fake_scan(library_ids):
            calls.append(("jellyfin", tuple(library_ids)))
            return {"scope": "targeted"}

        manager.refresh_rclone = fake_refresh
        manager.wait_for_mount = fake_mount
        manager.trigger_jellyfin_scan = fake_scan
        manager.observe_torrents(
            [
                {"hash": VALID_HASH, "name": "Movie", "progress": 0.2, "completionOn": 0, "category": "films"},
                {"hash": "b" * 40, "name": "Series", "progress": 0.2, "completionOn": 0, "category": "series"},
            ],
            allow_enqueue=False,
        )
        manager.observe_torrents(
            [
                {"hash": VALID_HASH, "name": "Movie", "progress": 1, "completionOn": 10, "category": "films"},
                {"hash": "b" * 40, "name": "Series", "progress": 1, "completionOn": 11, "category": "series"},
            ],
            allow_enqueue=True,
        )
        entries = await manager.process_pending_batch()
        self.assertEqual(len(entries), 2)
        self.assertEqual(calls[0], "rclone")
        self.assertEqual(calls[1], "mount")
        self.assertEqual(calls[2], ("jellyfin", ("lib-films", "lib-series")))

    async def test_retry_jellyfin_only_after_partial_failure(self):
        _qbit, manager = self.build_manager()

        async def fake_refresh():
            return None

        async def fake_mount():
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


if __name__ == "__main__":
    unittest.main()
