# Cloud Panel performance runtime

## Freshness and streaming

`GET /api/events?path=<relative-path>` streams one shared snapshot for each
active directory. The common runtime collects a directory once, then fans the
result out to every connected tab. Hidden pages pause the stream; reconnecting
receives the retained full snapshot before change-only `section` events resume.
If SSE fails, `/api/live` supplies the polling fallback.

The collector interval defaults to 500 ms (`CLOUD_PANEL_EVENTS_INTERVAL_SECONDS`).
Directory metadata remains cached for `CLOUD_PANEL_SCANDIR_CACHE_TTL` seconds,
default 0.5 seconds. The API exposes this upper bound as
`source_max_age_ms`. A `freshness` event updates the displayed bound using the
latest collection time; it does not resend or reload the directory listing.
Mutations bump a cache generation; scans started before the mutation may
finish for their waiters but cannot repopulate the new generation.

## Background jobs

`POST /api/jobs` submits `recursive_search`, `folder_sizes`,
`organize_preview`, `organize_apply`, or `copy`. `GET /api/jobs/{id}` returns
the shared job contract (`queued`, `running`, `completed`, `failed`, or
`interrupted`). Long operations use bounded worker threads and duplicate active
requests share the same job. Existing synchronous endpoints remain available.

Relevant settings:

- `CLOUD_PANEL_JOB_HEAVY_WORKERS` (default `2`)
- `CLOUD_PANEL_JOB_MAX_QUEUED` (default `32`)
- `CLOUD_PANEL_JOB_MAX_ENTRIES` (default `256`)
- `CLOUD_PANEL_JOB_STATE_PATH` (default `data/jobs-state.json`)

Jobs interrupted by a restart are retained as `interrupted`; mutations are not
replayed automatically.

## Targeted rclone refresh

Remote invalidation is opt-in. Set `CLOUD_PANEL_RCLONE_REFRESH_MODE=rc` and
point `CLOUD_PANEL_RCLONE_RC_URL` at the loopback RC endpoint. Mutations enqueue
only their parent directory with `recursive=false`; the queue is bounded and a
slow RC endpoint never delays the completed file operation. Optional RC
credentials use `CLOUD_PANEL_RCLONE_RC_USER` and
`CLOUD_PANEL_RCLONE_RC_PASS` and are never exposed to the browser.

Some rclone backends and versions do not support targeted VFS refresh. In that
case the local cache generation still invalidates immediately, while external
changes may remain invisible until the backend or rclone's normal polling
detects them. Monitor `targeted rclone refresh unavailable` in backend logs; the
message contains only the error class, never the RC URL or credentials.

## Static assets

`build.py` hashes the CSS, JavaScript, and font together, writes
`static/dist/asset-version.txt`, and rewrites HTML/CSS references to that token.
The server grants `private, max-age=31536000, immutable` only when the asset
query matches the current content token. Stale or unversioned assets, HTML,
configuration, and APIs use `no-store`.
