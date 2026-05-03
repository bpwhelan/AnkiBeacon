# AnkiBeacon

AnkiBeacon posts JSON events from Anki to local or remote HTTP receivers.

The current release emits `note_added` events when Anki creates notes through
`Collection.add_note()` or `Collection.add_notes()`. It also sends an optional
heartbeat so external apps can prefer push delivery while Anki is running and
fall back to polling only when the heartbeat expires.

The add-on package/folder name remains `new_card_created` for compatibility.
The public project name is `AnkiBeacon`, leaving room for more webhook event
types in future releases.

## Configuration

Edit the add-on config in Anki from Tools > Add-ons > AnkiBeacon > Config, or
edit `config.json` directly while developing.

```json
{
  "urls": [
    "http://127.0.0.1:7275/anki/events"
  ],
  "payload_mode": "note_id",
  "timeout_seconds": 5,
  "headers": {},
  "show_error_tooltips": true,
  "heartbeat_enabled": true,
  "heartbeat_interval_seconds": 10,
  "heartbeat_urls": [],
  "heartbeat_show_error_tooltips": false
}
```

If `heartbeat_urls` is empty, heartbeats are sent to the normal `urls` and the
receiver should branch on `event`.

## Events

`note_added`

```json
{
  "addon": "new_card_created",
  "addon_name": "AnkiBeacon",
  "protocol_version": 1,
  "session_id": "9b2b4eb1d7e046b5a2f916f35f2f9ef1",
  "event": "note_added",
  "source": "add_note",
  "created_at": "2026-04-22T23:00:00+00:00",
  "note_id": 1234567890,
  "deck_id": 987654321
}
```

`payload_mode: "note"` adds note fields, tags, card IDs, and basic card
metadata.

`heartbeat`

```json
{
  "addon": "new_card_created",
  "addon_name": "AnkiBeacon",
  "protocol_version": 1,
  "session_id": "9b2b4eb1d7e046b5a2f916f35f2f9ef1",
  "event": "heartbeat",
  "status": "ready",
  "sent_at": "2026-04-22T23:00:00+00:00",
  "heartbeat_interval_seconds": 10,
  "payload_mode": "note_id",
  "capabilities": ["heartbeat", "note_added"]
}
```

See `INTEGRATION.md` for receiver guidance.

## AnkiWeb Upload

Create the `.ankiaddon` archive from inside this folder so the archive root
contains files such as `__init__.py`, `config.json`, and `manifest.json`.

Do not include the containing `new_card_created` folder, `__pycache__` folders,
or local `meta.json` state in the upload archive.
