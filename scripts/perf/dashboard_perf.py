#!/usr/bin/env python3
"""Reproducible local before/after performance checks for the dashboard.

The benchmark runs paired baseline and current algorithms against the same
local fixtures. It measures sequential versus concurrent aggregation, separate
versus coalesced consumers, and full versus keyed browser rendering. It never
contacts qBittorrent, Prowlarr, Jellyfin, rclone, or the production VPS.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import importlib.util
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
for _import_root in (ROOT, ROOT / "torrent-panel"):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 2)


def duration_stats(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "mean_ms": round(statistics.fmean(values), 2) if values else 0.0,
        "min_ms": round(min(values), 2) if values else 0.0,
        "max_ms": round(max(values), 2) if values else 0.0,
    }


class _SyntheticUpstream:
    def __init__(self) -> None:
        self.calls = 0
        self.calls_lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._json({"ok": True})
                    return
                if parsed.path == "/slow":
                    query = parse_qs(parsed.query)
                    delay_ms = max(0, int(query.get("ms", ["0"])[0]))
                    with owner.calls_lock:
                        owner.calls += 1
                    time.sleep(delay_ms / 1000)
                    self._json({"delay_ms": delay_ms, "value": "sanitized-fixture"})
                    return
                self.send_error(404)

            def _json(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        class SyntheticHTTPServer(ThreadingHTTPServer):
            # The fan-out benchmark opens eight simultaneous loopback
            # connections; Python's small default listen backlog can reset
            # otherwise healthy requests on constrained runners.
            request_queue_size = 64

        self.server = SyntheticHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> "_SyntheticUpstream":
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path: str) -> dict[str, Any]:
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=5) as response:
            return json.load(response)


class _CoalescingReader:
    """Small reference coalescer used to exercise the accepted call contract."""

    def __init__(self, upstream: _SyntheticUpstream, *, delay_ms: int) -> None:
        self.upstream = upstream
        self.delay_ms = delay_ms
        self._lock = threading.Lock()
        self._pending: concurrent.futures.Future[dict[str, Any]] | None = None
        self._last_completed: tuple[float, concurrent.futures.Future[dict[str, Any]]] | None = None

    def read(self) -> dict[str, Any]:
        with self._lock:
            if self._pending is not None:
                pending = self._pending
                owner = False
            elif self._last_completed is not None and time.monotonic() - self._last_completed[0] < 0.05:
                pending = self._last_completed[1]
                owner = False
            else:
                pending = concurrent.futures.Future()
                self._pending = pending
                owner = True
        if owner:
            try:
                pending.set_result(self.upstream.get(f"/slow?ms={self.delay_ms}"))
            except Exception as exc:  # pragma: no cover - fixture failure path
                pending.set_exception(exc)
            with self._lock:
                if self._pending is pending:
                    self._pending = None
                self._last_completed = (time.monotonic(), pending)
        return pending.result(timeout=5)


def synthetic_http_benchmark(iterations: int, delay_ms: int) -> dict[str, Any]:
    baseline_serial: list[float] = []
    current_parallel: list[float] = []
    baseline_calls = 0
    current_calls = 0
    with _SyntheticUpstream() as upstream:
        for _ in range(iterations):
            calls_before = upstream.calls
            started = time.perf_counter()
            for _index in range(4):
                upstream.get(f"/slow?ms={delay_ms}")
            baseline_serial.append((time.perf_counter() - started) * 1000)
            baseline_calls += upstream.calls - calls_before

            calls_before = upstream.calls
            started = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(upstream.get, f"/slow?ms={delay_ms}") for _index in range(4)]
                for future in futures:
                    future.result(timeout=5)
            current_parallel.append((time.perf_counter() - started) * 1000)
            current_calls += upstream.calls - calls_before

        # Eight independent consumers model the baseline HTTP fan-out.
        baseline_calls_before = upstream.calls
        baseline_consumer_started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _index: upstream.get(f"/slow?ms={delay_ms}"), range(8)))
        baseline_consumers_ms = (time.perf_counter() - baseline_consumer_started) * 1000
        baseline_consumer_calls = upstream.calls - baseline_calls_before

        # Eight consumers share one current in-flight read.
        calls_before = upstream.calls
        reader = _CoalescingReader(upstream, delay_ms=delay_ms)
        current_consumer_started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _index: reader.read(), range(8)))
        current_consumers_ms = (time.perf_counter() - current_consumer_started) * 1000
        current_consumer_calls = upstream.calls - calls_before

    if current_consumer_calls != 1 or not all(item["value"] == "sanitized-fixture" for item in results):
        raise AssertionError("the synthetic coalescer did not share one upstream call")

    baseline_stats = duration_stats(baseline_serial)
    current_stats = duration_stats(current_parallel)
    return {
        "fixture": {
            "independent_requests": 4,
            "request_delay_ms": delay_ms,
            "iterations": iterations,
            "location": "local loopback HTTP fixture; no live service",
        },
        "baseline": {
            "source": "serial four-section aggregation and eight independent consumers",
            "aggregation": {**baseline_stats, "upstream_calls": baseline_calls},
            "eight_consumers": {
                "elapsed_ms": round(baseline_consumers_ms, 2),
                "upstream_calls": baseline_consumer_calls,
            },
        },
        "current": {
            "source": "concurrent four-section aggregation and one coalesced read shared by eight consumers",
            "aggregation": {**current_stats, "upstream_calls": current_calls},
            "eight_consumers": {
                "elapsed_ms": round(current_consumers_ms, 2),
                "upstream_calls": current_consumer_calls,
            },
        },
        "comparison": {
            "aggregation_p50_speedup": round(baseline_stats["p50_ms"] / max(1.0, current_stats["p50_ms"]), 2),
            "aggregation_call_reduction": baseline_calls - current_calls,
            "consumer_call_reduction": baseline_consumer_calls - current_consumer_calls,
        },
    }


async def actual_runtime_benchmark(delay_ms: int, rounds: int) -> dict[str, Any]:
    """Exercise the project runtime classes against deterministic async fixtures."""
    from common.performance import SharedSectionHub
    from torrent_panel.qbittorrent import AsyncTTLCache

    delay = delay_ms / 1000

    cache_calls = 0

    async def cache_factory() -> dict[str, Any]:
        nonlocal cache_calls
        cache_calls += 1
        await asyncio.sleep(delay)
        return {"value": "sanitized-fixture"}

    cache = AsyncTTLCache(60)
    cache_started = time.perf_counter()
    cache_values = await asyncio.gather(
        *(cache.get_or_set(cache_factory) for _ in range(8))
    )
    cache_first_burst_ms = (time.perf_counter() - cache_started) * 1000
    cache_hit_started = time.perf_counter()
    cache_hit_values = await asyncio.gather(
        *(cache.get_or_set(cache_factory) for _ in range(8))
    )
    cache_hit_ms = (time.perf_counter() - cache_hit_started) * 1000
    if cache_calls != 1 or cache_values != cache_hit_values:
        raise AssertionError("AsyncTTLCache did not coalesce and reuse the fixture")

    hub = SharedSectionHub(default_interval=60, heartbeat_interval=30)
    section_calls = {topic: 0 for topic in ("overview", "indexers", "health", "history")}

    def factory(topic: str):
        async def collect() -> dict[str, Any]:
            section_calls[topic] += 1
            await asyncio.sleep(delay)
            return {"topic": topic, "value": "sanitized-fixture"}

        return collect

    for topic in section_calls:
        hub.register(topic, factory(topic), interval=60)

    async def consume(stream) -> int:
        seen = 0
        try:
            async for _frame in stream:
                seen += 1
                if seen == 4:
                    return seen
        finally:
            await stream.aclose()
        return seen

    hub_started = time.perf_counter()
    seen = await asyncio.gather(
        consume(hub.stream(("overview", "indexers", "health", "history"))),
        consume(hub.stream(("overview", "indexers", "health", "history"))),
    )
    hub_elapsed_ms = (time.perf_counter() - hub_started) * 1000
    await hub.close()
    if seen != [4, 4] or any(count != 1 for count in section_calls.values()):
        raise AssertionError(f"SharedSectionHub fan-out mismatch: {section_calls}")

    invalidation_cache = AsyncTTLCache(60)
    invalidation_calls = 0

    async def invalidation_factory() -> dict[str, Any]:
        nonlocal invalidation_calls
        invalidation_calls += 1
        generation = invalidation_calls
        await asyncio.sleep(delay)
        return {"generation": generation}

    first_task = asyncio.create_task(invalidation_cache.get_or_set(invalidation_factory))
    await asyncio.sleep(delay / 4)
    invalidation_cache.invalidate()
    second = await invalidation_cache.get_or_set(invalidation_factory)
    first = await first_task
    third = await invalidation_cache.get_or_set(invalidation_factory)
    if first == second or second != third or invalidation_calls != 2:
        raise AssertionError("AsyncTTLCache invalidation generation contract failed")

    baseline_calls = 8
    return {
        "fixture": {
            "components": "project AsyncTTLCache and SharedSectionHub",
            "request_delay_ms": delay_ms,
            "rounds": rounds,
        },
        "baseline_contract": {
            "description": "each consumer starts its own section request",
            "consumer_calls_for_first_burst": baseline_calls,
        },
        "async_ttl_cache": {
            "consumers": 8,
            "first_burst_upstream_calls": cache_calls,
            "first_burst_elapsed_ms": round(cache_first_burst_ms, 2),
            "cached_burst_elapsed_ms": round(cache_hit_ms, 2),
            "call_reduction": baseline_calls - cache_calls,
            "invalidation_generation_ok": True,
        },
        "shared_section_hub": {
            "subscribers": 2,
            "sections": 4,
            "frames_per_subscriber": 4,
            "upstream_calls": section_calls,
            "elapsed_ms": round(hub_elapsed_ms, 2),
            "call_reduction": (2 * 4) - sum(section_calls.values()),
        },
    }


def _load_head_monitoring_module() -> Any:
    source = subprocess.run(
        ["git", "show", "HEAD:torrent-panel/torrent_panel/services/monitoring.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    module_name = "torrent_panel.services._performance_head_monitoring"
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="dashboard-performance-head-",
        delete=False,
        encoding="utf-8",
    ) as temporary:
        temporary.write(source)
        module_path = Path(temporary.name)
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise AssertionError("Could not load the HEAD monitoring module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        module_path.unlink(missing_ok=True)


async def monitoring_aggregation_benchmark(delay_ms: int, rounds: int) -> dict[str, Any]:
    """Measure HEAD and current monitoring through the real dashboard function."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from torrent_panel.services import monitoring as current

    baseline = _load_head_monitoring_module()
    delay = delay_ms / 1000

    class FakeQbit:
        async def torrents(self):
            await asyncio.sleep(delay)
            return [{"hash": "a" * 40, "name": "Fixture", "state": "uploading"}]

    class FakeMedia:
        def dashboard_alerts(self):
            return []

        def snapshot(self):
            return {"enabled": False, "entries": [], "notification": None}

    def fake_app() -> FastAPI:
        app = FastAPI()
        app.state.qbit = FakeQbit()
        app.state.media_automation = FakeMedia()
        app.state.monitoring_caches = {}
        app.state.service_checks = {}
        app.state.ratio_monitor = None
        return app

    async def stub_http(_app, name, _url, **_kwargs):
        await asyncio.sleep(delay)
        return {
            "name": name,
            "service": name,
            "status": "operational",
            "message": "Fixture",
            "checkedAt": "2026-01-01T00:00:00Z",
            "lastSuccessfulCheckAt": "2026-01-01T00:00:00Z",
            "action": {"kind": "open", "label": "Open", "url": "/"},
            "details": {},
        }

    async def stub_socket(_app, name, _host, _port, **_kwargs):
        await asyncio.sleep(delay)
        return {
            "name": name,
            "service": name,
            "status": "operational",
            "message": "Fixture",
            "checkedAt": "2026-01-01T00:00:00Z",
            "lastSuccessfulCheckAt": "2026-01-01T00:00:00Z",
            "action": {"kind": "open", "label": "Open", "url": "/"},
            "details": {},
        }

    async def stub_prowlarr():
        await asyncio.sleep(delay)
        return ({"connection": "ready", "indexersError": 0}, [])

    async def stub_storage(_app):
        await asyncio.sleep(delay)
        return {
            "disk": {"available": False},
            "rclone": {"transfersActive": 0, "errors": 0},
        }

    patches = {
        "http_service_status": stub_http,
        "socket_service_status": stub_socket,
        "prowlarr_snapshot": stub_prowlarr,
        "storage_snapshot": stub_storage,
    }
    originals: dict[tuple[Any, str], Any] = {}
    samples = {"baseline_head": [], "current": []}
    try:
        for label, module in (("baseline_head", baseline), ("current", current)):
            for name, replacement in patches.items():
                originals[(module, name)] = getattr(module, name)
                setattr(module, name, replacement)
            for _ in range(rounds):
                started = time.perf_counter()
                payload = await module._dashboard_snapshot_uncached(fake_app())
                samples[label].append((time.perf_counter() - started) * 1000)
                if not payload.get("services") or "overview" not in payload:
                    raise AssertionError("Monitoring benchmark returned an invalid dashboard")
    finally:
        for (module, name), original in originals.items():
            setattr(module, name, original)

    return {
        "fixture": {
            "delay_per_dependency_ms": delay_ms,
            "rounds": rounds,
            "services_stubbed": list(patches),
            "calls_real_functions": True,
        },
        "baseline_head": {
            "source": "git show HEAD:torrent-panel/torrent_panel/services/monitoring.py",
            **duration_stats(samples["baseline_head"]),
        },
        "current": {
            "source": "working-tree torrent_panel.services.monitoring",
            **duration_stats(samples["current"]),
        },
        "comparison": {
            "p50_change_percent": round(
                ((duration_stats(samples["current"])["p50_ms"] / max(0.01, duration_stats(samples["baseline_head"])["p50_ms"])) - 1) * 100,
                2,
            ),
            "separate_modules_loaded": baseline is not current,
        },
    }


def find_chrome() -> str | None:
    candidates = [
        os.environ.get("CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


def find_caddy() -> str | None:
    candidates = [
        os.environ.get("CADDY_BIN"),
        "/tmp/caddy",
        shutil.which("caddy"),
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


def render_benchmark(chrome: str, rows: int) -> dict[str, Any]:
    fixture = f"""<!doctype html>
<meta charset="utf-8">
<body><main id="rows"></main><pre id="report"></pre><script>
(() => {{
  const baselineSamples = [];
  const currentSamples = [];
  for (let iteration = 0; iteration < 20; iteration += 1) {{
    const started = performance.now();
    const fragment = document.createDocumentFragment();
    for (let index = 0; index < {rows}; index += 1) {{
      const row = document.createElement("div");
      row.className = "fixture-row";
      const name = document.createElement("span");
      name.textContent = "Archive " + index;
      const state = document.createElement("span");
      state.textContent = index % 2 ? "partage" : "telechargement";
      row.append(name, state);
      fragment.append(row);
    }}
    document.getElementById("rows").replaceChildren(fragment);
    baselineSamples.push(performance.now() - started);
  }}
  const rows = document.getElementById("rows");
  for (let iteration = 0; iteration < 20; iteration += 1) {{
    const started = performance.now();
    const nodes = rows.children;
    for (let index = 0; index < {rows}; index += 1) {{
      const row = nodes[index];
      const value = "Archive " + (index + iteration);
      if (row.firstChild.textContent !== value) row.firstChild.textContent = value;
    }}
    currentSamples.push(performance.now() - started);
  }}
  document.getElementById("report").textContent = JSON.stringify({{baselineSamples, currentSamples}});
}})();
</script>"""
    with tempfile.TemporaryDirectory(prefix="dashboard-perf-render-") as temp_dir:
        fixture_path = Path(temp_dir) / "render.html"
        fixture_path.write_text(fixture, encoding="utf-8")
        result = subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--virtual-time-budget=2000",
                f"--user-data-dir={Path(temp_dir) / 'profile'}",
                "--dump-dom",
                fixture_path.as_uri(),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    if result.returncode != 0:
        raise AssertionError(f"Chrome render benchmark failed: {result.stderr[-1000:]}")
    marker = '<pre id="report">'
    start = result.stdout.find(marker)
    if start < 0:
        raise AssertionError("Chrome render benchmark report missing")
    start += len(marker)
    end = result.stdout.find("</pre>", start)
    samples = json.loads(result.stdout[start:end].replace("&quot;", '"').replace("&amp;", "&"))
    baseline = [float(value) for value in samples["baselineSamples"]]
    current = [float(value) for value in samples["currentSamples"]]
    baseline_stats = duration_stats(baseline)
    current_stats = duration_stats(current)
    return {
        "rows_per_render": rows,
        "renders_each": len(baseline),
        "baseline": {"source": "full row replacement", **baseline_stats},
        "current": {"source": "in-place keyed text updates", **current_stats},
        "comparison": {
            "p50_speedup": round(baseline_stats["p50_ms"] / max(0.01, current_stats["p50_ms"]), 2),
        },
    }


def browser_contract_probe() -> dict[str, Any] | None:
    node = shutil.which("node")
    chrome = find_chrome()
    test_file = ROOT / "common" / "tests" / "browser_fluidity.test.js"
    if not node or not chrome or not test_file.is_file():
        return None
    env = os.environ.copy()
    env.update({"CHROME_PATH": chrome, "FLUIDITY_WIDTHS": "1440", "FLUIDITY_PANELS": "torrent,cloud,prowlarr"})
    started = time.perf_counter()
    result = subprocess.run(
        [node, str(test_file)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return {
        "ok": result.returncode == 0,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "stdout_tail": result.stdout.strip().splitlines()[-4:],
        "stderr_tail": result.stderr.strip().splitlines()[-4:],
    }


def live_sse_probe(chrome: str) -> dict[str, Any] | None:
    node = shutil.which("node")
    test_file = ROOT / "scripts" / "perf" / "live_integration.test.js"
    if not node or not test_file.is_file():
        return None
    env = os.environ.copy()
    env["CHROME_PATH"] = chrome
    started = time.perf_counter()
    result = subprocess.run(
        [node, str(test_file)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return {
        "ok": result.returncode == 0,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "stdout_tail": result.stdout.strip().splitlines()[-4:],
        "stderr_tail": result.stderr.strip().splitlines()[-4:],
    }


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def static_contract_checks(*, caddy_bin: str | None) -> list[Check]:
    caddy_config = read("caddy/dashboard.conf")
    homepage = read("homepage/services.yaml")
    rclone = read("rclone/override.conf")
    asset_paths = (
        "torrent-panel/torrent_panel/static/index.html",
        "prowlarr-panel/prowlarr_panel/static/index.html",
        "cloud-panel/cloud_panel/static/index.html",
    )
    assets = "\n".join(read(path) for path in asset_paths)
    panel_mains = "\n".join(
        read(path)
        for path in (
            "torrent-panel/torrent_panel/main.py",
            "prowlarr-panel/prowlarr_panel/main.py",
            "cloud-panel/cloud_panel/main.py",
        )
    )
    checks = [
        Check(
            "Caddy excludes SSE from compression",
            "not path */api/events" in caddy_config and "encode @compressible zstd gzip" in caddy_config,
            "event streams bypass encoding",
        ),
        Check(
            "Caddy flushes every panel event stream",
            caddy_config.count("flush_interval -1") == 8,
            f"found {caddy_config.count('flush_interval -1')} immediate-flush proxy blocks; expected 8",
        ),
        Check(
            "Caddy keeps runtime responses out of caches",
            "@runtime_data" in caddy_config and 'Cache-Control "no-store"' in caddy_config,
            "API and config.js responses are no-store",
        ),
        Check(
            "Caddy keeps authentication and security headers",
            "basic_auth @protected" in caddy_config
            and "Content-Security-Policy" in caddy_config
            and "Strict-Transport-Security" in caddy_config,
            "basic authentication, CSP, and HSTS remain configured",
        ),
        Check(
            "Homepage qBittorrent feature parity",
            "type: qbittorrent" in homepage
            and "enableLeechProgress: true" in homepage
            and "enableLeechSize: true" in homepage,
            "native leech progress and size remain enabled",
        ),
        Check(
            "Homepage Jellyfin feature parity",
            "type: jellyfin" in homepage
            and "enableBlocks: true" in homepage
            and "enableNowPlaying: true" in homepage,
            "native blocks and now-playing remain enabled",
        ),
        Check(
            "Homepage Prowlarr feature parity",
            "type: prowlarr" in homepage
            and "http://host.docker.internal:16124/prowlarr" in homepage
            and 'key: "{{HOMEPAGE_VAR_PROWLARR_API_KEY}}"' in homepage,
            "native Prowlarr statistics and configured API authentication remain enabled",
        ),
        Check(
            "Homepage reuses compatible storage aggregation",
            "url: http://host.docker.internal:3110/api/storage" in homepage
            and "http://host.docker.internal:5572/core/stats" not in homepage,
            "storage card calls the sanitized torrent-panel aggregate",
        ),
        Check(
            "rclone remote control remains non-destructive",
            "--rc " in rclone
            and "--rc-addr 127.0.0.1:5572" in rclone
            and "--rc-no-auth" in rclone
            and "--dir-cache-time 72h" in rclone
            and "--vfs-cache-max-size" not in rclone,
            "targeted refresh remains available with bounded defaults and no blind cache sizing",
        ),
        Check(
            "Panel static assets carry cache-busting queries",
            assets.count("static/dist/") >= 6
            and all("?v=" in line for line in assets.splitlines() if "static/dist/" in line),
            "panels expose versioned bundle URLs",
        ),
        Check(
            "Content-hash caching remains backend-owned",
            "immutable" not in caddy_config
            and panel_mains.count('max-age=31536000, immutable') >= 2,
            "Caddy only matches runtime data; panels validate content hashes",
        ),
    ]
    if caddy_bin:
        result = subprocess.run(
            [caddy_bin, "adapt", "--config", str(ROOT / "caddy/dashboard.conf"), "--adapter", "caddyfile"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        checks.append(Check("Caddy adapts", result.returncode == 0, (result.stderr or "configuration adapted").strip()[-500:]))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5, help="HTTP samples per aggregation pattern")
    parser.add_argument("--delay-ms", type=int, default=100, help="synthetic latency for each upstream request")
    parser.add_argument("--render-rows", type=int, default=300, help="rows created per browser render")
    parser.add_argument("--skip-browser", action="store_true", help="skip Chrome render and panel fixtures")
    parser.add_argument("--caddy-bin", default=None, help="optional path to caddy")
    parser.add_argument("--output", type=Path, help="write the complete JSON result to this path")
    args = parser.parse_args()
    if args.iterations < 1 or args.delay_ms < 1:
        parser.error("--iterations and --delay-ms must be positive")

    chrome = None if args.skip_browser else find_chrome()
    caddy = args.caddy_bin or find_caddy()
    runtime = asyncio.run(actual_runtime_benchmark(args.delay_ms, args.iterations))
    monitoring = asyncio.run(monitoring_aggregation_benchmark(args.delay_ms, args.iterations))
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "node": shutil.which("node"),
            "chrome": chrome,
            "caddy": caddy,
        },
        "synthetic_http": synthetic_http_benchmark(args.iterations, args.delay_ms),
        "actual_runtime": runtime,
        "monitoring_aggregation": monitoring,
        "render": render_benchmark(chrome, args.render_rows) if chrome else None,
        "browser_contract": None if args.skip_browser else browser_contract_probe(),
        "live_sse": None if args.skip_browser or not chrome else live_sse_probe(chrome),
        "checks": [check.__dict__ for check in static_contract_checks(caddy_bin=caddy)],
        "limitations": [
            "All figures are local synthetic measurements, not production VPS measurements.",
            "Baseline and current timings are measured in the same run on the same host.",
            "Browser contract duration includes Chrome startup and checks for all three panels.",
            "Production p50/p95 and resource use require docs/PERFORMANCE_DEPLOYMENT.md.",
        ],
    }

    failures = [check for check in report["checks"] if not check["ok"]]
    if report["browser_contract"] is not None and not report["browser_contract"]["ok"]:
        failures.append({"name": "Browser fluidity contract", "ok": False})
    if report["live_sse"] is not None and not report["live_sse"]["ok"]:
        failures.append({"name": "Bundle-level SSE integration", "ok": False})
    report["ok"] = not failures
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
