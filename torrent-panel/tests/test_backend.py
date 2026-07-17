import asyncio
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_panel.main import (  # noqa: E402
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

    async def torrents(self):
        return []

    async def pause_many(self, hashes):
        self.calls.append(("pause", hashes))

    async def resume_many(self, hashes):
        self.calls.append(("resume", hashes))

    async def delete_many(self, hashes, delete_files):
        self.calls.append(("delete", hashes, delete_files))

    async def add_magnet(self, magnet, **kwargs):
        self.calls.append(("add", magnet, kwargs))

    async def ready(self):
        return True


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.original_qbit = app.state.qbit
        self.original_limiter = app.state.action_limiter
        self.original_csrf_tokens = dict(app.state.csrf_tokens)
        app.state.qbit = FakeQbit()
        app.state.action_limiter = RateLimiter(max_calls=100, period_seconds=60, max_keys=100)
        app.state.csrf_tokens = {}
        self.client = TestClient(app)
        session = self.client.get("/torrent-panel/api/session").json()
        self.csrf = session["csrfToken"]

    def tearDown(self):
        app.state.qbit = self.original_qbit
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


if __name__ == "__main__":
    unittest.main()
