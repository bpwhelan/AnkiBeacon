from __future__ import annotations

import json
import threading
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Iterable

from anki.collection import Collection
from anki.notes import Note
from aqt import gui_hooks, mw
from aqt.qt import QTimer
from aqt.utils import tooltip

ADDON_NAME = "AnkiBeacon"
ADDON_PACKAGE = "new_card_created"
PROTOCOL_VERSION = 1
SESSION_ID = uuid.uuid4().hex
HEARTBEAT_TIMER: QTimer | None = None

DEFAULT_CONFIG = {
    "urls": [],
    "payload_mode": "note_id",
    "timeout_seconds": 5,
    "headers": {},
    "show_error_tooltips": True,
    "heartbeat_enabled": True,
    "heartbeat_interval_seconds": 10,
    "heartbeat_urls": [],
    "heartbeat_show_error_tooltips": False,
}

VALID_PAYLOAD_MODES = {"note_id", "note"}


def log(message: str) -> None:
    print(f"[{ADDON_NAME}] {message}")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def merged_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if mw and mw.addonManager:
        user_config = mw.addonManager.getConfig(__name__) or {}
        if isinstance(user_config, dict):
            config.update(user_config)

    urls = config.get("urls")
    if not isinstance(urls, list):
        urls = []
    config["urls"] = [str(url).strip() for url in urls if str(url).strip()]

    headers = config.get("headers")
    if not isinstance(headers, dict):
        headers = {}
    config["headers"] = {str(key): str(value) for key, value in headers.items()}

    payload_mode = str(config.get("payload_mode", DEFAULT_CONFIG["payload_mode"]))
    if payload_mode not in VALID_PAYLOAD_MODES:
        payload_mode = DEFAULT_CONFIG["payload_mode"]
    config["payload_mode"] = payload_mode

    timeout_seconds = config.get("timeout_seconds", DEFAULT_CONFIG["timeout_seconds"])
    try:
        config["timeout_seconds"] = max(1, float(timeout_seconds))
    except (TypeError, ValueError):
        config["timeout_seconds"] = DEFAULT_CONFIG["timeout_seconds"]

    config["show_error_tooltips"] = bool(
        config.get("show_error_tooltips", DEFAULT_CONFIG["show_error_tooltips"])
    )

    heartbeat_urls = config.get("heartbeat_urls")
    if not isinstance(heartbeat_urls, list):
        heartbeat_urls = []
    config["heartbeat_urls"] = [
        str(url).strip() for url in heartbeat_urls if str(url).strip()
    ]

    heartbeat_interval_seconds = config.get(
        "heartbeat_interval_seconds",
        DEFAULT_CONFIG["heartbeat_interval_seconds"],
    )
    try:
        config["heartbeat_interval_seconds"] = max(1.0, float(heartbeat_interval_seconds))
    except (TypeError, ValueError):
        config["heartbeat_interval_seconds"] = DEFAULT_CONFIG["heartbeat_interval_seconds"]

    config["heartbeat_enabled"] = bool(
        config.get("heartbeat_enabled", DEFAULT_CONFIG["heartbeat_enabled"])
    )
    config["heartbeat_show_error_tooltips"] = bool(
        config.get(
            "heartbeat_show_error_tooltips",
            DEFAULT_CONFIG["heartbeat_show_error_tooltips"],
        )
    )
    return config


def show_error(message: str, show_tooltips: bool) -> None:
    log(message)
    if not show_tooltips:
        return

    if not mw:
        return

    try:
        mw.taskman.run_on_main(lambda: tooltip(message, parent=mw))
    except Exception:
        log("failed to show tooltip")


def build_note_payload(note: Note, deck_id: Any, source: str, payload_mode: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "addon": ADDON_PACKAGE,
        "addon_name": ADDON_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "session_id": SESSION_ID,
        "event": "note_added",
        "source": source,
        "created_at": utc_now_iso(),
        "note_id": int(note.id),
    }

    if deck_id is not None:
        try:
            payload["deck_id"] = int(deck_id)
        except (TypeError, ValueError):
            payload["deck_id"] = deck_id

    if payload_mode == "note":
        note_type = note.note_type() or {}
        cards = note.cards()
        payload.update(
            {
                "note_type_id": int(note.mid),
                "note_type_name": note_type.get("name"),
                "tags": list(note.tags),
                "fields": {name: value for name, value in note.items()},
                "card_ids": [int(card.id) for card in cards],
                "cards": [
                    {
                        "card_id": int(card.id),
                        "template_index": int(card.ord),
                        "deck_id": int(card.did),
                    }
                    for card in cards
                ],
            }
        )

    return payload


def build_heartbeat_payload(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "addon": ADDON_PACKAGE,
        "addon_name": ADDON_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "session_id": SESSION_ID,
        "event": "heartbeat",
        "status": "ready",
        "sent_at": utc_now_iso(),
        "heartbeat_interval_seconds": config["heartbeat_interval_seconds"],
        "payload_mode": config["payload_mode"],
        "capabilities": ["heartbeat", "note_added"],
    }


def describe_payload(payload: dict[str, Any]) -> str:
    event = payload.get("event", "payload")
    if event == "note_added":
        return f"note {payload.get('note_id')}"
    return str(event)


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> None:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request_headers.update(headers)

    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response.read()


def dispatch_payloads(
    payloads: list[dict[str, Any]],
    urls: list[str],
    headers: dict[str, str],
    timeout_seconds: float,
    show_tooltips: bool,
    thread_name: str,
) -> None:
    if not urls or not payloads:
        return

    def worker() -> None:
        for payload in payloads:
            for url in urls:
                try:
                    post_json(
                        url=url,
                        payload=payload,
                        headers=headers,
                        timeout_seconds=timeout_seconds,
                    )
                    log(f"posted {describe_payload(payload)} to {url}")
                except urllib.error.HTTPError as err:
                    body = err.read().decode("utf-8", errors="replace").strip()
                    detail = f"HTTP {err.code}"
                    if body:
                        detail = f"{detail}: {body[:250]}"
                    # show_error(
                    #     f"{payload.get('event', 'payload')} failed for {url} ({detail})",
                    #     show_tooltips,
                    # )
                except Exception as err:
                    log(traceback.format_exc())
                    # show_error(
                    #     f"{payload.get('event', 'payload')} failed for {url} ({err})",
                    #     show_tooltips,
                    # )

    threading.Thread(
        target=worker,
        name=thread_name,
        daemon=True,
    ).start()


def queue_note_webhook(note: Note, deck_id: Any, source: str) -> None:
    config = merged_config()
    payload = build_note_payload(
        note=note,
        deck_id=deck_id,
        source=source,
        payload_mode=config["payload_mode"],
    )
    dispatch_payloads(
        payloads=[payload],
        urls=config["urls"],
        headers=config["headers"],
        timeout_seconds=config["timeout_seconds"],
        show_tooltips=config["show_error_tooltips"],
        thread_name="anki_beacon_note_webhook",
    )


def queue_heartbeat() -> None:
    config = merged_config()
    if not config["heartbeat_enabled"]:
        return

    urls = config["heartbeat_urls"] or config["urls"]
    payload = build_heartbeat_payload(config)
    dispatch_payloads(
        payloads=[payload],
        urls=urls,
        headers=config["headers"],
        timeout_seconds=config["timeout_seconds"],
        show_tooltips=config["heartbeat_show_error_tooltips"],
        thread_name="anki_beacon_heartbeat",
    )


def start_heartbeat_timer() -> None:
    global HEARTBEAT_TIMER

    if not mw:
        return

    config = merged_config()
    if not config["heartbeat_enabled"]:
        return

    existing_timer = getattr(mw, "_anki_beacon_heartbeat_timer", None)
    if existing_timer is not None and existing_timer.isActive():
        HEARTBEAT_TIMER = existing_timer
        return
    if existing_timer is not None:
        existing_timer.stop()

    timer = QTimer(mw)
    timer.setInterval(int(config["heartbeat_interval_seconds"] * 1000))
    timer.timeout.connect(queue_heartbeat)
    timer.start()

    HEARTBEAT_TIMER = timer
    mw._anki_beacon_heartbeat_timer = timer
    queue_heartbeat()
    log(f"heartbeat timer started ({config['heartbeat_interval_seconds']}s)")


def patch_collection() -> None:
    if getattr(Collection, "_anki_beacon_patched", False):
        return

    original_add_note = Collection.add_note
    original_add_notes = Collection.add_notes

    @wraps(original_add_note)
    def add_note_with_webhook(self: Collection, note: Note, deck_id: Any):
        changes = original_add_note(self, note, deck_id)
        queue_note_webhook(note=note, deck_id=deck_id, source="add_note")
        return changes

    @wraps(original_add_notes)
    def add_notes_with_webhook(self: Collection, requests: Iterable[Any]):
        buffered_requests = list(requests)
        changes = original_add_notes(self, buffered_requests)
        config = merged_config()
        payload_mode = config["payload_mode"]
        payloads = [
            build_note_payload(
                note=request.note,
                deck_id=getattr(request, "deck_id", None),
                source="add_notes",
                payload_mode=payload_mode,
            )
            for request in buffered_requests
        ]
        dispatch_payloads(
            payloads=payloads,
            urls=config["urls"],
            headers=config["headers"],
            timeout_seconds=config["timeout_seconds"],
            show_tooltips=config["show_error_tooltips"],
            thread_name="anki_beacon_note_webhook",
        )
        return changes

    Collection.add_note = add_note_with_webhook
    Collection.add_notes = add_notes_with_webhook
    Collection._anki_beacon_patched = True


patch_collection()
gui_hooks.main_window_did_init.append(start_heartbeat_timer)
