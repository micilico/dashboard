#!/usr/bin/env node
"use strict";

// Real-browser SSE integration for the three generated panel bundles. Every
// backend and event stream is mocked in-page; no configured service is called.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const root = path.resolve(__dirname, "../..");
const chrome = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const output = fs.mkdtempSync(path.join(os.tmpdir(), "dashboard-live-integration-"));

function browserHarness(panel) {
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const ensure = (condition, message) => { if (!condition) throw new Error(message); };
  const stamp = "2026-10-26T10:00:00Z";
  let page = 1;
  const calls = [];
  const events = [];

  class MockEventSource {
    static instances = [];
    constructor(url) {
      this.url = String(url);
      this.listeners = new Map();
      this.closed = false;
      MockEventSource.instances.push(this);
      setTimeout(() => this.onopen?.(), 0);
    }
    addEventListener(name, handler) { this.listeners.set(name, handler); }
    removeEventListener(name, handler) {
      if (this.listeners.get(name) === handler) this.listeners.delete(name);
    }
    emit(name, payload) {
      events.push({ name, payload });
      this.listeners.get(name)?.({ data: JSON.stringify(payload) });
    }
    close() { this.closed = true; }
  }

  const torrent = (name, index = 0) => ({
    hash: String(index + 1).padStart(40, "a"), name, state: "downloading",
    progress: 0.5, downloadSpeed: 1024, uploadSpeed: 512, ratio: 1,
    size: 4096, downloaded: 2048, uploaded: 2048, remaining: 2048,
    eta: 60, addedOn: 1700000000, completionOn: 0, category: "Films", tags: "",
  });

  function responseFor(url, options) {
    const parsed = new URL(url, window.location.href);
    const route = parsed.pathname;
    const method = options?.method || "GET";
    calls.push({ route, method, search: parsed.search });
    if (route.endsWith("/api/session")) return { csrfToken: "synthetic-token" };
    if (route.endsWith("/api/torrents")) return { torrents: [torrent("Initial torrent")] };
    if (route.endsWith("/api/dashboard")) return {
      overview: { downloadSpeedBytes: 1024, uploadSpeedBytes: 512, activeTorrents: 1 },
      services: [], alerts: [], recentActivity: [], criticalCount: 0,
      storage: {}, mediaAutomation: { enabled: false, entries: [], notification: null },
    };
    if (route.endsWith("/api/storage")) return { disk: {}, rclone: {} };
    if (route.endsWith("/api/activity")) return { summary: {}, timeline: [] };
    if (route.endsWith("/api/torrents/stats/trackers")) return { stats: { totals: [], daily: [] } };
    if (route.endsWith("/api/torrents/relink-status")) return { count: 0 };
    if (route.endsWith("/api/trackers/index")) return { torrents: [] };
    if (route.endsWith("/api/overview")) return {
      connection: "ready", version: "fixture", indexersActive: 1, indexersError: 0,
      applicationsTotal: 0, systemWarnings: 0, refreshedAt: stamp,
    };
    if (route.endsWith("/api/indexers")) return {
      indexers: [{ id: 1, name: "Initial indexer", enabled: true, health: "ok", protocol: "torrent", tags: [] }],
      refreshedAt: stamp,
    };
    if (route.endsWith("/api/applications")) return { applications: [], refreshedAt: stamp };
    if (route.endsWith("/api/health")) return {
      alerts: [{ type: "warning", message: "Health seed" }], refreshedAt: stamp,
    };
    if (route.endsWith("/api/history")) return {
      events: [{ id: 1, title: "History seed" }], refreshedAt: stamp,
    };
    if (route.endsWith("/api/files")) {
      page = Math.floor(Number(parsed.searchParams.get("offset") || 0) / 50) + 1;
      const live = calls.filter((call) => call.route.endsWith("/api/files")).length > 1;
      return {
        items: [{ name: live ? "Page 2 live" : "Page 2 seed", path: `library/file-${page}-${live ? "live" : "seed"}.txt`, is_dir: false, size_bytes: 10, size: "10 o", modified: 1700000000 }],
        total: 120, has_more: true, current_path: "library", disk_available: false,
      };
    }
    if (route.endsWith("/api/favorites")) return { items: [], favorites: [] };
    if (route.endsWith("/api/live")) return { path: "library", fingerprint: "v2", observed_at: Date.now() };
    return {};
  }

  window.__liveMock = { calls, events, MockEventSource, get page() { return page; } };
  window.EventSource = MockEventSource;
  window.fetch = async (url, options = {}) => new Response(JSON.stringify(responseFor(String(url), options)), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

  async function waitFor(predicate, label, timeout = 3500) {
    const started = performance.now();
    while (!predicate()) {
      if (performance.now() - started > timeout) throw new Error(`timeout: ${label}`);
      await delay(20);
    }
  }

  async function source() {
    await waitFor(() => MockEventSource.instances.length > 0, "EventSource connection");
    return MockEventSource.instances.at(-1);
  }

  async function torrentCheck() {
    await waitFor(() => document.querySelector("#torrentRows")?.textContent.includes("Initial torrent"), "initial torrent row");
    const stream = await source();
    stream.emit("section", {
      type: "section", topic: "torrents", collectedAt: stamp, sequence: 2, stale: false,
      data: { torrents: [torrent("SSE torrent", 2)] },
    });
    await waitFor(() => document.querySelector("#torrentRows")?.textContent.includes("SSE torrent"), "SSE torrent render");
    return ["torrent bundle rendered an SSE section update"];
  }

  async function prowlarrCheck() {
    await waitFor(() => document.querySelector("#indexersView")?.textContent.includes("Initial indexer"), "initial indexer");
    document.querySelector("#healthTab")?.click();
    await waitFor(() => document.querySelector("#alertsList")?.textContent.includes("Health seed"), "health seed");
    await waitFor(() => document.querySelector("#historyList")?.textContent.includes("History seed"), "history seed");
    const stream = await source();
    stream.emit("section", {
      type: "section", topic: "health", collectedAt: stamp, sequence: 2, stale: false,
      data: { alerts: [{ type: "warning", message: "Health live" }] },
    });
    await waitFor(() => document.querySelector("#alertsList")?.textContent.includes("Health live"), "SSE health render");
    ensure(/History seed/.test(document.querySelector("#historyList")?.textContent || ""), "history was wiped by a health update");
    stream.emit("section", {
      type: "section", topic: "history", collectedAt: stamp, sequence: 3, stale: false,
      data: { events: [{ id: 2, title: "History live" }] },
    });
    await waitFor(() => document.querySelector("#historyList")?.textContent.includes("History live"), "SSE history render");
    ensure(/Health live/.test(document.querySelector("#alertsList")?.textContent || ""), "health was wiped by a history update");
    const prowlarrFetches = calls.length;
    stream.emit("freshness", {
      type: "freshness", topic: "health", collectedAt: "2026-10-26T10:00:05Z",
      sourceUpdatedAt: stamp, sequence: 4, stale: false, unchanged: true,
    });
    await delay(100);
    ensure(calls.length === prowlarrFetches, "Prowlarr freshness signal fetched or replaced a full section");
    ensure(/Health live/.test(document.querySelector("#alertsList")?.textContent || ""), "Prowlarr freshness signal replaced health data");
    return ["prowlarr SSE health/history sections remained independent"];
  }

  async function cloudCheck() {
    await waitFor(() => document.querySelector("#fileBody")?.textContent.includes("Page 2 seed"), "cloud page two");
    const stream = await source();
    stream.emit("snapshot", {
      type: "snapshot", topic: "files-library", collectedAt: stamp, sequence: 1, stale: false,
      data: { path: "library", fingerprint: "v1", source_age_ms: 0, source_scanned_at: stamp },
    });
    stream.emit("section", {
      type: "section", topic: "files-library", collectedAt: stamp, sequence: 2, stale: false,
      data: { path: "library", fingerprint: "v2", source_age_ms: 0, source_scanned_at: stamp },
    });
    await waitFor(() => document.querySelector("#fileBody")?.textContent.includes("Page 2 live"), "cloud SSE refresh");
    ensure(/Page 2 \/ 3/.test(document.querySelector("#pageInfo")?.textContent || ""), "cloud page changed after SSE refresh");
    const fileRequests = calls.filter((call) => call.route.endsWith("/api/files"));
    ensure(fileRequests.some((call) => new URLSearchParams(call.search).get("offset") === "50"), "cloud refresh did not request page-two offset");
    const requestCount = calls.length;
    stream.emit("freshness", {
      type: "freshness", topic: "files-library", collectedAt: "2026-10-26T10:00:05Z",
      sequence: 3, stale: false, unchanged: true,
    });
    await delay(100);
    ensure(calls.length === requestCount, "Cloud freshness signal triggered a directory reload");
    return ["cloud SSE refresh preserved page two and requested offset 50"];
  }

  const runners = { torrent: torrentCheck, prowlarr: prowlarrCheck, cloud: cloudCheck };
  window.addEventListener("load", () => {
    setTimeout(async () => {
      const result = { ok: false, checks: [], errors: [] };
      window.addEventListener("error", (event) => result.errors.push(String(event.message)));
      window.addEventListener("unhandledrejection", (event) => result.errors.push(String(event.reason)));
      try {
        result.checks.push(...await runners[panel]());
        result.ok = result.checks.length > 0 && result.errors.length === 0;
      } catch (error) {
        result.errors.push(String(error?.stack || error));
      }
      const report = document.createElement("pre");
      report.id = "live-report";
      report.hidden = true;
      report.textContent = JSON.stringify(result);
      document.body.append(report);
    }, 500);
  });
}

const panels = {
  torrent: {
    directory: "torrent-panel/torrent_panel/static",
    config: { publicPrefix: "" },
    query: "?view=torrents",
  },
  prowlarr: {
    directory: "prowlarr-panel/prowlarr_panel/static",
    config: { publicPrefix: "", prowlarrPanelPrefix: "" },
    query: "?view=indexers",
  },
  cloud: {
    directory: "cloud-panel/cloud_panel/static",
    config: { publicPrefix: "", cloudPanelPrefix: "" },
    query: "?view=files&path=library&page=2",
  },
};

const selectedPanels = (process.env.LIVE_PANELS || "torrent,prowlarr,cloud").split(",").filter(Boolean);
for (const panel of selectedPanels) {
  const definition = panels[panel];
  assert.ok(definition, `Unknown panel: ${panel}`);
  const staticDirectory = path.join(root, definition.directory);
  let html = fs.readFileSync(path.join(staticDirectory, "index.html"), "utf8");
  const css = pathToFileURL(path.join(staticDirectory, "dist/app.min.css")).href;
  const bundle = pathToFileURL(path.join(staticDirectory, "dist/app.min.js")).href;
  html = html.replace(/<link[^>]+app\.min\.css[^>]*>/i, `<link rel="stylesheet" href="${css}">`);
  html = html.replace(/<script[^>]+config\.js[^>]*><\/script>/i, `<script>window.__${panel.toUpperCase()}_PANEL_CONFIG__ = ${JSON.stringify(definition.config)};</script>`);
  html = html.replace(/<script[^>]+app\.min\.js[^>]*><\/script>/i, "");
  html = html.replace("</body>", `<script>(${browserHarness.toString()})(${JSON.stringify(panel)});</script><script src="${bundle}"></script></body>`);
  const fixture = path.join(output, `${panel}.html`);
  fs.writeFileSync(fixture, html, "utf8");
  const target = `${pathToFileURL(fixture).href}${definition.query}`;
  const result = spawnSync(chrome, [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files",
    `--user-data-dir=${path.join(output, `profile-${panel}`)}`,
    "--virtual-time-budget=8000", "--dump-dom", target,
  ], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024, timeout: 30000 });
  assert.equal(
    result.status,
    0,
    result.error?.message || result.stderr || `Chrome exited via ${result.signal || "unknown signal"}`,
  );
  const match = result.stdout.match(/<pre id="live-report"[^>]*>([\s\S]*?)<\/pre>/);
  assert.ok(match, `${panel}: live browser report missing`);
  const report = JSON.parse(match[1].replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"));
  assert.equal(report.ok, true, `${panel}: ${JSON.stringify(report, null, 2)}`);
  console.log(`${panel}: ${report.checks.join(", ")}`);
}

console.log(`Live SSE artifacts: ${output}`);
