import sys
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prowlarr_panel.main import RateLimiter, app, cleanup_csrf_tokens, parse_rate_limits  # noqa: E402
from prowlarr_panel.prowlarr import ProwlarrClient, ProwlarrConfig, ProwlarrError, scrub  # noqa: E402


class FakeProwlarr:
    def __init__(self):
        self.calls = []
        self.capabilities = {"detected": True, "version": "1.2.3", "endpoints": {"download": True}}

    async def ready(self):
        return True

    async def overview(self):
        return {
            "connection": "ready",
            "version": "1.2.3",
            "indexersTotal": 1,
            "indexersActive": 1,
            "indexersDisabled": 0,
            "indexersError": 0,
            "applicationsTotal": 1,
            "systemWarnings": 0,
            "lastSuccessfulRefresh": "2026-07-17T00:00:00Z",
            "capabilities": self.capabilities,
        }

    async def indexers(self):
        return [{"id": 7, "name": "Safe", "enabled": True, "health": "ok", "protocol": "torrent", "tags": []}]

    async def applications(self):
        return []

    async def health(self):
        return []

    async def history(self):
        return []

    async def discover(self):
        self.calls.append(("discover",))
        return self.capabilities

    async def test_indexer(self, indexer_id=None):
        self.calls.append(("test", indexer_id))
        return {"status": "accepted", "result": {}}

    async def set_indexer_enabled(self, indexer_id, enabled):
        self.calls.append(("enabled", indexer_id, enabled))
        return {"id": indexer_id, "enabled": enabled}

    async def search(self, query, categories, indexer_ids):
        self.calls.append(("search", query, categories, indexer_ids))
        return {"results": [], "partialFailures": []}

    async def grab(self, release_id):
        self.calls.append(("grab", release_id))
        return {"status": "sent", "release": {}}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.original_prowlarr = app.state.prowlarr
        self.original_limiters = app.state.limiters
        self.original_csrf_tokens = dict(app.state.csrf_tokens)
        app.state.prowlarr = FakeProwlarr()
        app.state.limiters = {name: RateLimiter(max_calls=100, period_seconds=60, max_keys=100) for name in ("search", "test", "modify", "grab")}
        app.state.csrf_tokens = {}
        self.client = TestClient(app)
        self.csrf = self.client.get("/prowlarr-panel/api/session").json()["csrfToken"]

    def tearDown(self):
        app.state.prowlarr = self.original_prowlarr
        app.state.limiters = self.original_limiters
        app.state.csrf_tokens = self.original_csrf_tokens

    def post_action(self, path, payload):
        return self.client.post(path, json=payload, headers={"X-Prowlarr-Panel-CSRF": self.csrf})

    def test_session_response_is_not_cacheable(self):
        response = self.client.get("/prowlarr-panel/api/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_csrf_required_for_actions(self):
        response = self.client.post("/prowlarr-panel/api/search", json={"query": "ubuntu"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "csrf_expired")

    def test_search_uses_separate_limiter(self):
        app.state.limiters["search"] = RateLimiter(max_calls=1, period_seconds=60, max_keys=10)
        first = self.post_action("/prowlarr-panel/api/search", {"query": "ubuntu"})
        second = self.post_action("/prowlarr-panel/api/search", {"query": "debian"})
        test = self.post_action("/prowlarr-panel/api/indexers/test", {"id": 7})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(test.status_code, 200)

    def test_search_drops_zero_category_from_stale_frontend(self):
        response = self.post_action("/prowlarr-panel/api/search", {"query": "dragon", "categories": [0, 2000], "indexerIds": [0, 7]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app.state.prowlarr.calls[-1], ("search", "dragon", [2000], [7]))

    def test_indexer_actions_are_validated(self):
        response = self.post_action("/prowlarr-panel/api/indexers/test", {"id": 0})
        self.assertEqual(response.status_code, 422)

    def test_grab_sends_only_opaque_release_fields(self):
        response = self.post_action("/prowlarr-panel/api/grab", {"releaseId": "opaque-release-id-123", "title": "Example"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app.state.prowlarr.calls[-1], ("grab", "opaque-release-id-123"))

    def test_expired_csrf_token_is_rejected(self):
        token = self.csrf
        app.state.csrf_tokens[token] = -1_000_000
        cleanup_csrf_tokens(app, now=1_000_000)
        response = self.client.post(
            "/prowlarr-panel/api/search",
            json={"query": "ubuntu"},
            headers={"X-Prowlarr-Panel-CSRF": token, "Cookie": f"prowlarr_panel_csrf={token}"},
        )
        self.assertEqual(response.status_code, 403)


class MappingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.close()

    def test_scrub_removes_sensitive_fields_and_private_urls(self):
        payload = {
            "name": "Indexer",
            "apiKey": "secret",
            "downloadUrl": "https://tracker.test/download?passkey=secret",
            "message": "failed https://tracker.test/rss?passkey=secret",
        }
        cleaned = scrub(payload)
        self.assertNotIn("apiKey", cleaned)
        self.assertNotIn("downloadUrl", cleaned)
        self.assertNotIn("secret", str(cleaned))
        self.assertIn("[URL masquée]", cleaned["message"])

    def test_indexer_mapping_hides_field_values(self):
        self.client = ProwlarrClient(ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret"))
        mapped = self.client._map_indexer(
            {
                "id": 1,
                "name": "Private",
                "protocol": "torrent",
                "enable": True,
                "fields": [{"name": "passkey", "value": "secret", "errorMessage": "bad https://tracker.test/rss?passkey=secret"}],
            }
        )
        self.assertEqual(mapped["health"], "error")
        self.assertNotIn("secret", str(mapped))
        self.assertNotIn("tracker.test", str(mapped))

    def test_rate_limit_parser_keeps_defaults(self):
        parsed = parse_rate_limits("search=2/10,grab=3/20")
        self.assertEqual(parsed["search"], (2, 10))
        self.assertEqual(parsed["grab"], (3, 20))
        self.assertIn("modify", parsed)

    async def test_test_indexer_posts_full_indexer_model(self):
        self.client = ProwlarrClient(ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret"))
        calls = []

        async def fake_json(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {"id": 7, "name": "Safe", "fields": [{"name": "passkey", "value": "secret"}]}

        class FakeResponse:
            content = b"{}"

            def json(self):
                return {}

        async def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return FakeResponse()

        self.client._json = fake_json
        self.client._request = fake_request
        result = await self.client.test_indexer(7)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(calls[0][0:2], ("GET", "/api/v1/indexer/7"))
        self.assertEqual(calls[1][0:2], ("POST", "/api/v1/indexer/test"))
        self.assertEqual(calls[1][2]["json"]["id"], 7)

    async def test_test_all_indexers_uses_testall_endpoint(self):
        self.client = ProwlarrClient(ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret"))
        calls = []

        class FakeResponse:
            content = b"{}"

            def json(self):
                return {}

        async def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return FakeResponse()

        self.client._request = fake_request
        result = await self.client.test_indexer()
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(calls[-1][0:2], ("POST", "/api/v1/indexer/testall"))

    async def test_validation_error_message_is_scrubbed(self):
        self.client = ProwlarrClient(ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret"))

        class FakeResponse:
            status_code = 400

            def json(self):
                return [{"errorMessage": "bad https://tracker.test/rss?passkey=secret"}]

        async def fake_client_request(*args, **kwargs):
            return FakeResponse()

        self.client._client.request = fake_client_request
        with self.assertRaises(ProwlarrError) as context:
            await self.client._request("POST", "/api/v1/indexer/test", json={"id": 7})
        self.assertEqual(context.exception.code, "prowlarr_validation_refused")
        self.assertNotIn("secret", context.exception.public_message)
        self.assertNotIn("tracker.test", context.exception.public_message)

    async def test_search_prefers_post_search_input(self):
        self.client = ProwlarrClient(ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret"))
        calls = []

        async def fake_json(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return [{"title": "Ubuntu", "indexerId": 7, "guid": "opaque"}]

        self.client._json = fake_json
        result = await self.client.search("ubuntu", [2000], [7])
        self.assertEqual(len(result["results"]), 1)
        self.assertNotEqual(result["results"][0]["id"], "7:opaque")
        self.assertEqual(calls[-1][0:2], ("POST", "/api/v1/search"))
        self.assertEqual(calls[-1][2]["json"]["query"], "ubuntu")
        self.assertEqual(calls[-1][2]["json"]["type"], "search")
        self.assertEqual(calls[-1][2]["json"]["categories"], [2000])
        self.assertEqual(calls[-1][2]["json"]["indexerIds"], [7])

    async def test_search_falls_back_to_get_when_post_is_unavailable(self):
        self.client = ProwlarrClient(ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret"))
        calls = []

        async def fake_json(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "POST":
                raise ProwlarrError(
                    404,
                    "Action Prowlarr indisponible sur cette version.",
                    code="prowlarr_action_unavailable",
                )
            return [{"title": "Ubuntu", "indexerId": 7, "guid": "opaque"}]

        self.client._json = fake_json
        result = await self.client.search("ubuntu", [2000], [7])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(calls[0][0:2], ("POST", "/api/v1/search"))
        self.assertEqual(calls[1][0:2], ("GET", "/api/v1/search"))
        self.assertIn(("categories", "2000"), calls[1][2]["params"])
        self.assertIn(("indexerIds", "7"), calls[1][2]["params"])

    async def test_grab_uses_cached_release_download_url_and_torrent_panel(self):
        self.client = ProwlarrClient(ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret"))
        release_id = "opaque-release-id"
        calls = []

        class FakeResponse:
            content = b"{}"
            status_code = 200

            def json(self):
                return {}

        async def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return FakeResponse()

        self.client._request = fake_request
        self.client._release_cache[release_id] = (
            time.monotonic(),
            {"title": "Ubuntu", "downloadUrl": "https://tracker.test/download?passkey=secret"},
        )
        grab = await self.client.grab(release_id)
        self.assertEqual(grab["status"], "sent")
        self.assertEqual(calls[-1][0:2], ("POST", "/api/v1/search"))
        self.assertEqual(calls[-1][2]["json"]["downloadUrl"], "https://tracker.test/download?passkey=secret")

    async def test_grab_requires_cached_release(self):
        self.client = ProwlarrClient(ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret"))
        with self.assertRaises(ProwlarrError) as context:
            await self.client.grab("missing-release-id")
        self.assertEqual(context.exception.code, "release_expired")


if __name__ == "__main__":
    unittest.main()
