import asyncio
import httpx
import sys
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prowlarr_panel.main import ASSET_VERSIONS, RateLimiter, app, build_live_hub, cleanup_csrf_tokens, parse_live_topics, parse_rate_limits  # noqa: E402
from prowlarr_panel.prowlarr import AsyncSnapshotCache, ProwlarrClient, ProwlarrConfig, ProwlarrError, scrub  # noqa: E402


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

    async def cached_overview(self, force=False):
        return await self.overview()

    async def indexers(self):
        return [{"id": 7, "name": "Safe", "enabled": True, "health": "ok", "protocol": "torrent", "tags": []}]

    async def cached_indexers(self, force=False):
        return await self.indexers()

    async def applications(self):
        return []

    async def cached_applications(self, force=False):
        return await self.applications()

    async def health(self):
        return []

    async def cached_health(self, force=False):
        return await self.health()

    async def history(self):
        return []

    async def cached_history(self, force=False):
        return await self.history()

    def section_updated_at(self, name):
        return "2026-07-17T00:00:00Z"

    async def close(self):
        pass

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
        self.assertIn("httponly", response.headers["set-cookie"].lower())

    def test_content_hashed_assets_are_privately_cached_and_mismatches_are_not(self):
        version = ASSET_VERSIONS["/static/dist/app.min.js"]
        current = self.client.get(f"/prowlarr-panel/static/dist/app.min.js?v={version}")
        stale = self.client.get("/prowlarr-panel/static/dist/app.min.js?v=obsolete")
        self.assertIn("private", current.headers["cache-control"])
        self.assertIn("immutable", current.headers["cache-control"])
        self.assertNotIn("immutable", stale.headers.get("cache-control", ""))

    def test_html_and_config_are_not_cacheable(self):
        for path in ("/prowlarr-panel/", "/prowlarr-panel/config.js"):
            response = self.client.get(path)
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

    async def test_snapshot_coalesces_and_invalidates(self):
        self.client = ProwlarrClient(
            ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret", snapshot_cache_ttl_seconds=60)
        )
        calls = {"status": 0, "indexers": 0, "applications": 0, "health": 0}

        async def status():
            calls["status"] += 1
            return {"version": "1"}

        async def indexers():
            calls["indexers"] += 1
            return []

        async def applications():
            calls["applications"] += 1
            return []

        async def health():
            calls["health"] += 1
            return []

        self.client.system_status = status
        self.client.indexers = indexers
        self.client.applications = applications
        self.client.health = health
        await asyncio.gather(self.client.snapshot(), self.client.snapshot())
        self.assertEqual(calls, {"status": 1, "indexers": 1, "applications": 1, "health": 1})
        self.client.invalidate_snapshot()
        await self.client.snapshot()
        self.assertEqual(calls, {"status": 2, "indexers": 2, "applications": 2, "health": 2})

    async def test_snapshot_invalidation_discards_collection_epoch(self):
        cache = AsyncSnapshotCache(60)
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                await release_first.wait()
                return "stale"
            return "fresh"

        pending = asyncio.create_task(cache.get(factory))
        await first_started.wait()
        cache.invalidate()
        release_first.set()
        self.assertEqual(await pending, "fresh")
        self.assertEqual(calls, 2)

    async def test_cancelled_waiter_does_not_cancel_shared_collection(self):
        cache = AsyncSnapshotCache(60)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"ok": True}

        first = asyncio.create_task(cache.get(factory))
        await started.wait()
        second = asyncio.create_task(cache.get(factory))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        self.assertEqual(await second, {"ok": True})
        self.assertEqual(calls, 1)

    async def test_shared_live_hub_publishes_indexers_without_overview(self):
        fake = FakeProwlarr()
        indexer_calls = 0

        async def cached_indexers(force=False):
            nonlocal indexer_calls
            indexer_calls += 1
            return await fake.indexers()

        async def cached_overview(force=False):
            raise AssertionError("an indexers subscription must not collect overview")

        fake.cached_indexers = cached_indexers
        fake.cached_overview = cached_overview
        hub = build_live_hub(fake)
        await hub.start()
        stream = hub.stream(("indexers",))
        frame = await asyncio.wait_for(anext(stream), timeout=1)
        self.assertEqual(frame["data"]["topic"], "indexers")
        self.assertIn("indexers", frame["data"]["data"])
        self.assertEqual(indexer_calls, 1)
        await stream.aclose()
        await hub.close()

    async def test_live_unchanged_section_ignores_source_timestamp_for_fingerprint(self):
        fake = FakeProwlarr()
        timestamp = "2026-07-17T00:00:00Z"
        fake.section_updated_at = lambda _name: timestamp
        hub = build_live_hub(fake)
        await hub.start()
        stream = hub.stream(("indexers",))
        first = await asyncio.wait_for(anext(stream), timeout=1)
        self.assertEqual(first["event"], "snapshot")
        timestamp = "2026-07-17T00:00:01Z"
        await hub.refresh("indexers")
        unchanged = await asyncio.wait_for(anext(stream), timeout=1)
        self.assertEqual(unchanged["event"], "freshness")
        self.assertEqual(unchanged["data"]["topic"], "indexers")
        await stream.aclose()
        await hub.close()

    async def test_live_health_and_history_use_rest_payload_shape(self):
        fake = FakeProwlarr()

        async def health():
            return [{"message": "safe"}]

        async def cached_health(force=False):
            return await health()

        fake.cached_health = cached_health
        hub = build_live_hub(fake)
        await hub.start()
        health_stream = hub.stream(("health",))
        health_frame = await asyncio.wait_for(anext(health_stream), timeout=1)
        self.assertIn("alerts", health_frame["data"]["data"])
        self.assertNotIn("health", health_frame["data"]["data"])
        await health_stream.aclose()
        history_stream = hub.stream(("history",))
        history_frame = await asyncio.wait_for(anext(history_stream), timeout=1)
        self.assertIn("events", history_frame["data"]["data"])
        self.assertNotIn("history", history_frame["data"]["data"])
        await history_stream.aclose()
        await hub.close()

    async def test_sse_payload_is_sanitized_and_topics_are_bounded(self):
        encoded = str(scrub({
            "topic": "indexers",
            "data": [{"name": "safe", "apiKey": "secret", "message": "https://tracker.test/?passkey=secret"}],
        }))
        self.assertNotIn("secret", encoded)
        self.assertNotIn("apiKey", encoded)
        self.assertEqual(parse_live_topics("indexers,unknown,indexers"), ("indexers",))

    async def test_release_cache_evicts_oldest_handles_and_keeps_maximum(self):
        self.client = ProwlarrClient(ProwlarrConfig(url="http://127.0.0.1:1/prowlarr", api_key="secret"))
        now = time.monotonic()
        for index in range(501):
            self.client._release_cache[f"handle-{index}"] = (now, {"title": str(index)})
        self.client._cleanup_release_cache()
        self.assertEqual(len(self.client._release_cache), 500)
        self.assertNotIn("handle-0", self.client._release_cache)
        self.assertIn("handle-500", self.client._release_cache)

    async def test_localhost_fallback_client_is_reused_after_primary_failure(self):
        self.client = ProwlarrClient(
            ProwlarrConfig(url="http://host.docker.internal:16124/prowlarr", api_key="secret")
        )
        calls = []

        class Response:
            status_code = 200

        class Primary:
            async def request(self, *args, **kwargs):
                calls.append("primary")
                raise httpx.ConnectError("primary down")

            async def aclose(self):
                pass

        class Fallback:
            async def request(self, *args, **kwargs):
                calls.append("fallback")
                return Response()

            async def aclose(self):
                pass

        self.client._client = Primary()
        self.client._fallback_client = Fallback()
        response = await self.client._request("GET", "/api/v1/system/status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client._using_fallback)
        self.assertEqual(calls, ["primary", "fallback"])

    async def test_ambiguous_mutation_is_never_replayed_on_localhost_fallback(self):
        self.client = ProwlarrClient(
            ProwlarrConfig(url="http://host.docker.internal:16124/prowlarr", api_key="secret")
        )
        calls = []

        class Primary:
            async def request(self, *args, **kwargs):
                calls.append("primary")
                raise httpx.ReadError("connection dropped after send")

            async def aclose(self):
                pass

        class Fallback:
            async def request(self, *args, **kwargs):
                calls.append("fallback")
                raise AssertionError("mutations must not be replayed")

            async def aclose(self):
                pass

        self.client._client = Primary()
        self.client._fallback_client = Fallback()
        with self.assertRaises(ProwlarrError) as context:
            await self.client._request("POST", "/api/v1/search", json={"query": "ubuntu"})
        self.assertEqual(context.exception.code, "ssh_tunnel_unavailable")
        self.assertEqual(calls, ["primary"])

    async def test_fallback_response_uses_normal_status_validation(self):
        self.client = ProwlarrClient(
            ProwlarrConfig(url="http://host.docker.internal:16124/prowlarr", api_key="secret")
        )

        class Response:
            status_code = 401

            def json(self):
                return {}

        class Primary:
            async def request(self, *args, **kwargs):
                raise httpx.ConnectError("primary down")

            async def aclose(self):
                pass

        class Fallback:
            async def request(self, *args, **kwargs):
                return Response()

            async def aclose(self):
                pass

        self.client._client = Primary()
        self.client._fallback_client = Fallback()
        with self.assertRaises(ProwlarrError) as context:
            await self.client._request("GET", "/api/v1/system/status")
        self.assertEqual(context.exception.code, "prowlarr_auth_refused")

    async def test_mutation_connect_failure_can_use_fallback_once(self):
        self.client = ProwlarrClient(
            ProwlarrConfig(url="http://host.docker.internal:16124/prowlarr", api_key="secret")
        )
        calls = []

        class Response:
            status_code = 202
            content = b"{}"

            def json(self):
                return {}

        class Primary:
            async def request(self, *args, **kwargs):
                calls.append("primary")
                raise httpx.ConnectError("connection was not established")

            async def aclose(self):
                pass

        class Fallback:
            async def request(self, *args, **kwargs):
                calls.append("fallback")
                return Response()

            async def aclose(self):
                pass

        self.client._client = Primary()
        self.client._fallback_client = Fallback()
        response = await self.client._request(
            "POST",
            "/api/v1/search",
            json={"query": "ubuntu"},
            acceptable={200, 202},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(calls, ["primary", "fallback"])

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
