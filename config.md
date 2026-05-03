# AnkiBeacon Configuration

Set `urls` to one or more HTTP endpoints that should receive JSON webhook
events.

- `urls`: endpoints for `note_added` events.
- `payload_mode`: `note_id` sends IDs only; `note` also sends fields, tags,
  card IDs, and card metadata.
- `timeout_seconds`: request timeout for each webhook POST.
- `headers`: extra HTTP headers, for example an authorization header.
- `show_error_tooltips`: shows note webhook errors in Anki tooltips.
- `heartbeat_enabled`: sends periodic `heartbeat` events while Anki is running.
- `heartbeat_interval_seconds`: heartbeat interval in seconds.
- `heartbeat_urls`: optional separate endpoints for heartbeats. Leave empty to
  send heartbeats to `urls`.
- `heartbeat_show_error_tooltips`: shows heartbeat errors in Anki tooltips.
