# Performance deployment and evidence

This document defines how latency and rendering changes are measured,
deployed, and rolled back. Measurements in this repository are local
synthetic evidence unless a result explicitly includes a VPS host, date,
commit, and resource counters.

## Performance contracts

### Live data

- Each panel exposes `GET /api/events` below its configured public prefix.
  The torrent-panel console prefixes also forward `/api/events` through Caddy.
- Event responses use `text/event-stream`, `Cache-Control: no-store`, and a
  periodic `heartbeat` frame. Payloads contain only the same sanitized fields
  accepted by the panel UI.
- All panels share the `snapshot`, `section`, `freshness`, `job`, and
  `heartbeat` protocol. An unchanged section emits only a lightweight
  `freshness` signal; `heartbeat` is a transport keepalive. A reconnect
  receives a full `snapshot`, and consumers retain polling as a fallback.
  Prowlarr and torrent sections, Cloud directory changes and bounded
  background jobs all use this common format.
- A freshness signal reports collector activity, not necessarily an upstream
  refresh. Prowlarr continues to display its last successful source timestamp;
  Cloud updates the age of the last directory scan without applying a full
  listing payload.
- Caddy bypasses compression for `*/api/events` and sets
  `flush_interval -1` for every proxied event route. All other compression,
  `basic_auth`, CSP, HSTS, and browser security headers remain in force.

### HTTP caching

- APIs and `config.js` are `no-store` so a reconnect never reuses stale state
  or CSRF configuration.
- Caddy does not infer immutable caching from a query parameter. Each panel
  validates the requested bundle version against its current content hash and
  applies private immutable caching only to the exact active version.
- HTML remains dynamically revalidated. The browser must always discover the
  current bundle version before reusing an earlier immutable response.

### Homepage

The qBittorrent, Prowlarr, and Jellyfin widgets remain native because that is
the only supported configuration that preserves leech progress/size, Prowlarr
activity, Jellyfin media blocks, and now-playing. The existing Homepage
`customapi` contract is used only for the compatible storage card, which now
reads the sanitized torrent-panel storage aggregate instead of calling the
rclone RC endpoint directly. Custom API intervals remain supported and are
not treated as an SSE substitute.

## Reproducible local benchmark

The benchmark uses the project Python environment, real `SharedSectionHub` and
`AsyncTTLCache` classes, a local mock upstream, Chrome, and the loaded panel
bundles. Its baseline monitoring implementation is read from Git HEAD into an
isolated temporary module. It never contacts a real panel, qBittorrent,
Prowlarr, Jellyfin, rclone, or the VPS.

~~~sh
.venv/bin/python scripts/perf/dashboard_perf.py \
  --iterations 5 \
  --delay-ms 100 \
  --caddy-bin /path/to/caddy \
  --output /tmp/dashboard-perf.json
~~~

The result contains:

- paired baseline/current latency for four synthetic upstream calls executed
  serially and concurrently;
- the actual `AsyncTTLCache` upstream call count when eight consumers share an
  in-flight read, plus its immediate cache-hit latency;
- the actual `SharedSectionHub` fan-out and call count for two subscribers and
  four sections;
- real Torrent monitoring aggregation loaded both from Git HEAD and from the
  working tree, using the same mocked dependency delays;
- browser-only timing for full 300-row replacement versus keyed in-place
  updates;
- the pass/fail result and duration of the existing real-browser panel
  fluidity contract;
- static checks for SSE flushing, cache headers, security headers, Homepage
  feature parity, storage aggregation reuse, rclone RC availability, and
  cache-busting asset URLs;
- an optional real `caddy adapt` parse when `--caddy-bin` or `CADDY_BIN` is set.

Use `--skip-browser` on hosts without Chrome. The synthetic speedup is an
architecture check, not a claim about production panel latency.

The bundle-level SSE test is separate:

~~~sh
node scripts/perf/live_integration.test.js
~~~

It loads the three generated bundles in Chrome and verifies Torrent section
updates, independent Prowlarr health/history updates, and Cloud preservation of
the current page and offset after a directory event. Unchanged freshness
signals must not replace full section payloads.

## VPS measurement procedure

Run the same procedure before and after the deployment, on the same host and
network conditions. Record the Git commit and UTC timestamps for both runs.

1. Capture idle and active CPU, memory, block I/O, and network counters for the
   three panels with `docker stats --no-stream`. Capture host free space and
   iowait before and after the run.
2. Measure at least 30 warm requests and 10 cold requests for each page and
   API section through Caddy. Keep p50 and p95; do not publish only averages.
3. Exercise one mutation per panel and verify that its related data is
   invalidated and refreshed without a full-process reload.
4. Open `/api/events` for each panel through Caddy and inject a sanitized test
   change. Record time from source update to the browser's visible update,
   separating collection delay from render delay.
5. Make one secondary dependency artificially slow without changing production
   services. The primary section must remain usable while the slow section is
   pending.
6. Run the browser suites at 1600, 1440, 1024, 768, and 375 px with
   `FLUIDITY_SCREENSHOTS=1` and inspect every generated image.
7. Store counters and latency tables in the deployment record. Include the
   commands used, sample count, failures, and observed limits. Never infer a
   VPS result from the local synthetic report.

The browser QA command is:

~~~sh
FLUIDITY_SCREENSHOTS=1 FLUIDITY_WIDTHS=1600,1440,1024,768,375 \
  node common/tests/browser_fluidity.test.js
~~~

## rclone freshness and sizing

The mount keeps its existing `--dir-cache-time 72h`, `--vfs-cache-mode full`,
RC listener on loopback, and non-destructive operation. The dashboard should
use targeted `vfs/refresh` for the directories it changes. Do not restart
rclone on a schedule.

For backends without change notifications, externally created or moved files
may remain invisible for up to the 72-hour directory-cache window. If that is
unacceptable, first test a shorter cache duration on a controlled mount and
record listing latency. External writers can also request a targeted refresh
of the paths they modify.

Do not set a large VFS cache maximum or a very long cache age by default.
Measure the active working set, free space on the host, upload/download peaks,
and eviction activity. Choose cache limits from that evidence while preserving
enough temporary space for uploads, atomic writes, and Docker.

## Rollout

1. Record the currently deployed commit and take the normal application-state
   backup before changing services.
2. Build and test the three panels. Confirm every HTML file names the current
   content-hash bundle version. Deploy the panels before enabling a Caddy rule
   that expects a new event route.
3. Install the Caddyfile in a staging path, run `caddy validate`, then
   `caddy adapt`. Reload Caddy after validation. Keep the previous Caddyfile
   available for rollback.
4. Deploy Homepage configuration only after verifying the panel storage API
   returns a successful sanitized response from the Homepage container.
5. Leave rclone and autossh running. There is no performance justification in
   this change for restarting the mount or tunnel; their existing configuration
   stays in place.
6. Run the cold/warm measurements, SSE freshness test, mutation checks, panel
   tests, and five-width visual QA. Compare the result with the recorded
   baseline and document any site-specific remote latency.

## Rollback

Restore the previous panel images and generated bundles first. Restore the
previous Caddyfile and reload Caddy; do not restart Caddy or rclone when a
reload is sufficient. Restore the previous Homepage service configuration if
the aggregate endpoint is unavailable. Revert panel data volumes only when a
failed migration changed their schema; the performance change does not require
a data migration.

After rollback, confirm `healthz`, `readyz`, the primary navigation, one safe
read per panel, and one CSRF-protected mutation. Keep the failed measurement
report and logs so the next attempt starts from evidence.
