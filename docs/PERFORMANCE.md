# Performance defaults

The panels use small in-process caches. They are intentionally local and bounded;
they do not change the API response shapes or expose backend credentials.

| Data | Default TTL |
| --- | ---: |
| qBittorrent `torrents/info` snapshot | 6 s |
| Tracker index | 300 s |
| Torrent dashboard | 8 s |
| rclone and Ultra storage | 20 s |
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
