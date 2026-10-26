# Performance defaults

The dashboard favours fresh data while bounding work. Active data uses short
TTL caches and in-flight request coalescing; expensive or stable data keeps
longer caches. Caches remain local to each process and expose no backend
credentials or internal service URLs.

| Data | Default freshness strategy |
| --- | --- |
| qBittorrent torrent state | 0.5 s TTL; incremental `sync/maindata` with list fallback |
| Torrent dashboard, health, activity and storage | 0.5 s snapshots |
| Prowlarr per-section snapshots | 0.5 s, shared by HTTP and SSE consumers |
| Cloud active-directory scan | 0.5 s TTL, concurrent scans coalesced |
| Cloud recursive search | 5 s cache for an identical query |
| Tracker index | 300 s, invalidated by tracker mutations |
| Library inventory and media metadata | 180 s and 3600 s respectively |

The default SSE collection interval is 0.5 s for the torrent, Prowlarr and
cloud sections. Collections do not overlap. Failures back off progressively,
and polling resumes when an event stream cannot stay connected. This interval
is a collection cadence, not a promise that an upstream service or remote
filesystem can answer within 0.5 s.

## Live protocol

All three panels expose `GET /api/events` under their configured public prefix.
SSE uses the following event names and JSON frame types:

- `snapshot`: the first complete state for a topic, including `topic`, `data`,
  `sequence`, `collectedAt`, `changedAt` and `stale`;
- `section`: a later changed state for one topic;
- `freshness`: a lightweight `topic`/`collectedAt`/`stale` notice for an
  unchanged payload; it never carries the large section body;
- `job`: a bounded background-job update;
- `heartbeat`: a transport keepalive, separate from data freshness.

Only sanitized panel data is serialized. Reconnecting clients receive the
retained complete snapshot. Collector time is not treated as an upstream
refresh time: Prowlarr retains its source timestamp, and Cloud recomputes the
age of its last directory scan without reloading the listing. Hidden pages
close their stream, and each panel retains a polling fallback. Every event
response is `no-store`; Caddy disables
compression and uses immediate flushing for all eight event proxy paths.

## Backend and browser behaviour

Independent monitoring services are collected concurrently. A slow secondary
service therefore does not serialize the whole dashboard. qBittorrent uses its
incremental synchronization endpoint and rebuilds state after reconnect;
mutation paths invalidate the related caches.

The browser applies only the sections that changed, avoids replacing matching
rows, and batches repeated rendering work. Selection, focus, open menus and
scroll position survive refreshes. Torrent input and cloud name search debounce
for 250 ms. Lists remain bounded for rendering while pagination or controls
continue to expose the complete result set.

Cloud recursive searches, folder-size scans, copies and long organization tasks
run in bounded background queues. Interactive file operations use a separate
thread capacity. A restarted process marks unfinished work as interrupted and
does not replay a mutation automatically.

## rclone and static assets

The rclone mount keeps `--dir-cache-time 72h`, full VFS cache mode and its
loopback RC listener. The dashboard requests targeted non-recursive refreshes
for directories it mutates and never restarts the mount on a schedule. Some
backends cannot report external changes promptly; those paths remain limited
by the mount cache until an external writer requests a targeted refresh or the
cache is deliberately shortened after measurement.

HTML and `config.js` remain revalidated. APIs are `no-store`. Built CSS/JS use a
content-hash query, and each panel applies private immutable caching only when
the requested version equals its current bundle hash. Caddy does not infer
immutable caching from an arbitrary query string.

## Local verification

The reproducible benchmark exercises real project runtime classes plus a
baseline loaded from `git show HEAD:torrent-panel/torrent_panel/services/monitoring.py`.
It runs the same synthetic upstream delays for both versions and performs a
real-browser keyed-render comparison.

```sh
.venv/bin/python scripts/perf/dashboard_perf.py \
  --iterations 5 \
  --delay-ms 100 \
  --caddy-bin /tmp/caddy \
  --output /tmp/dashboard-perf.json
```

The report includes the project `SharedSectionHub`, `AsyncTTLCache`, Torrent
monitoring aggregation, actual Caddy adaptation and static configuration
checks. It is local evidence only; it never contacts configured services and
does not claim production VPS latency.

Run the real bundle-level SSE checks separately:

```sh
node scripts/perf/live_integration.test.js
```

The test loads each generated panel bundle in Chrome, mocks its HTTP and SSE
transport, emits real `snapshot`/`section`/`freshness` frames, and verifies
Torrent row updates, independent Prowlarr health/history updates, and Cloud
page-two preservation. Unchanged freshness frames must not redraw full section
payloads. It uses the generated bundles, so rebuild them first after any
frontend change.

```sh
.venv/bin/python torrent-panel/build.py
.venv/bin/python prowlarr-panel/build.py
.venv/bin/python cloud-panel/build.py
node common/tests/browser_fluidity.test.js
FLUIDITY_SCREENSHOTS=1 FLUIDITY_WIDTHS=1600,1440,1024,768,375 \
  node common/tests/browser_fluidity.test.js
```

Production p50/p95, resource counters and source-to-render timing must be
recorded on the VPS with [PERFORMANCE_DEPLOYMENT.md](PERFORMANCE_DEPLOYMENT.md).
