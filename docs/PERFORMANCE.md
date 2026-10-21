# Performance defaults

The panels use small in-process caches. They are intentionally local and bounded;
they do not change the API response shapes or expose backend credentials.

| Data | Default TTL |
| --- | ---: |
| qBittorrent `torrents/info` snapshot | 6 s |
| Tracker index | 300 s |
| Torrent dashboard | 8 s |
| rclone storage snapshot | 20 s |
| Activity, health and statistics | 30 s |
| Prowlarr and Jellyfin monitoring | 45 s |
| Media metadata lookup | 3600 s, max 256 entries |

Mutating qBittorrent actions invalidate the torrent snapshot. Adding/removing
torrents and tracker changes also invalidate the tracker index. Frontend manual
refreshes use the existing force path. Cloud filesystem caches are bounded by
`CLOUD_PANEL_SCANDIR_CACHE_MAX_ENTRIES` and
`CLOUD_PANEL_FOLDER_SIZE_CACHE_MAX_ENTRIES`; recursive search remains capped by
`CLOUD_PANEL_SEARCH_MAX_RESULTS` and requires three characters by default.
Per-path mutation locks are also capped by `CLOUD_PANEL_PATH_LOCK_MAX_ENTRIES`;
only unlocked entries are evicted.
Statistics and tracker deltas stay in memory between flushes and are atomically
flushed at most every 15 seconds by default and always at application shutdown.

For a quick VPS check, compare the upstream request counters in the test mocks
or access logs with `docker stats --no-stream torrent-panel prowlarr-panel cloud-panel`.
The chosen trade-off is a few seconds of freshness for substantially fewer
duplicate network requests and filesystem walks.

## Frontend request and rendering defaults

The shared browser API client gives each request a 10 s timeout by default.
Safe reads can retry up to three times with exponential backoff; mutations are
not replayed after an ambiguous network failure. A caller can cancel requests,
including a pending retry delay. CSRF expiry refreshes the session and retries
the original mutation once. Long library organization actions override the
timeout explicitly and disable mutation retries.

Torrent search and cloud filename search debounce keystrokes for 250 ms. Each
panel coalesces overlapping refreshes, applies only the latest requested state,
and suspends its periodic refresh while the document is hidden. Prowlarr polls
every 30 s; torrent refresh uses the user's saved interval (9 s by default).
Cloud file rows are requested/rendered 50 at a time, Prowlarr release results
render 80 at a time, and torrent rows render in bounded windows of 300. These
are UI limits: pagination still exposes the complete available result set.
Refresh reconciliation updates existing rows where possible so focus,
selection, open menus and scroll position survive unchanged data refreshes.

## Local verification

Rebuild generated assets after frontend changes:

```sh
python3 torrent-panel/build.py
python3 prowlarr-panel/build.py
python3 cloud-panel/build.py
```

Run the shared API/browser contracts and panel frontend suites:

```sh
node common/tests/api_client.test.js
node common/tests/ui_patterns.test.js
node torrent-panel/tests/frontend_logic.test.js
node prowlarr-panel/tests/frontend_logic.test.js
node cloud-panel/tests/frontend_logic.test.js
node common/tests/browser_fluidity.test.js
node common/tests/visual_breakpoints.test.js
```

The browser fixtures use synthetic API responses and do not contact configured
services. To save screenshots at the five checked widths, run:

```sh
FLUIDITY_SCREENSHOTS=1 FLUIDITY_WIDTHS=1600,1440,1024,768,375 \
  node common/tests/browser_fluidity.test.js
```

These checks cover behavior, geometry, and local render output. They do not
measure production latency, VPS resource usage, or real-service load; use
access logs and `docker stats --no-stream torrent-panel prowlarr-panel cloud-panel`
for an on-host operational comparison.
