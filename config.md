# AnkiBeacon Configuration

Configuration is organized around operation objects. Each object declares the
operation it handles, the URLs that receive it, and any operation-specific
options.

```json
{
  "defaults": {
    "timeout_seconds": 5,
    "headers": {},
    "show_error_tooltips": true
  },
  "operations": [
    {
      "operation": "note_added",
      "enabled": true,
      "urls": [
        "http://127.0.0.1:7275/anki/events"
      ],
      "payload_mode": "note_id"
    },
    {
      "operation": "heartbeat",
      "enabled": true,
      "urls": [],
      "fallback_operation": "note_added",
      "interval_seconds": 10,
      "show_error_tooltips": false
    }
  ]
}
```

- `defaults`: shared settings used by operations unless an operation overrides
  them.
- `operations`: enabled event streams. Future events can be added as more
  operation objects.
- `operation`: event/operation name, currently `note_added` or `heartbeat`.
- `enabled`: set to `false` to disable that operation.
- `urls`: HTTP endpoints that should receive JSON webhook events for that
  operation.
- `payload_mode`: `note_id` sends IDs only; `note` also sends fields, tags,
  card IDs, and card metadata. Used by `note_added`.
- `timeout_seconds`: request timeout for each webhook POST.
- `headers`: extra HTTP headers, for example an authorization header.
- `show_error_tooltips`: shows webhook errors in Anki tooltips.
- `fallback_operation`: operation to borrow URLs from when `urls` is empty.
  Used by `heartbeat` so heartbeats can share `note_added` endpoints.
- `interval_seconds`: heartbeat interval in seconds. Used by `heartbeat`.

Older flat configs using `urls`, `payload_mode`, `heartbeat_urls`, and related
top-level keys are still accepted and translated into operation objects at
runtime.
