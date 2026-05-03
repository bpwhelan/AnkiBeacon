from __future__ import annotations

import json
import threading
import traceback
import urllib.error
import urllib.request
import uuid
from copy import deepcopy
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

NOTE_ADDED_OPERATION = "note_added"
HEARTBEAT_OPERATION = "heartbeat"

DEFAULT_CONFIG = {
    "defaults": {
        "timeout_seconds": 5,
        "headers": {},
        "show_error_tooltips": True,
    },
    "operations": [
        {
            "operation": NOTE_ADDED_OPERATION,
            "enabled": True,
            "urls": [],
            "payload_mode": "note_id",
        },
        {
            "operation": HEARTBEAT_OPERATION,
            "enabled": True,
            "urls": [],
            "fallback_operation": NOTE_ADDED_OPERATION,
            "interval_seconds": 10,
            "show_error_tooltips": False,
        },
    ],
}

VALID_PAYLOAD_MODES = {"note_id", "note"}
DEFAULT_OPERATION_BY_NAME = {
    str(operation["operation"]): operation for operation in DEFAULT_CONFIG["operations"]
}
LEGACY_CONFIG_KEYS = {
    "urls",
    "payload_mode",
    "timeout_seconds",
    "headers",
    "show_error_tooltips",
    "heartbeat_enabled",
    "heartbeat_interval_seconds",
    "heartbeat_urls",
    "heartbeat_show_error_tooltips",
}


def log(message: str) -> None:
    print(f"[{ADDON_NAME}] {message}")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cleaned_urls(urls: Any) -> list[str]:
    if not isinstance(urls, list):
        return []
    return [str(url).strip() for url in urls if str(url).strip()]


def cleaned_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {str(key): str(value) for key, value in headers.items()}


def cleaned_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def cleaned_seconds(value: Any, default: float, minimum: float = 1.0) -> float:
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return default


def cleaned_payload_mode(value: Any) -> str:
    payload_mode = str(value)
    if payload_mode not in VALID_PAYLOAD_MODES:
        return "note_id"
    return payload_mode


def read_user_config() -> dict[str, Any]:
    if not mw or not mw.addonManager:
        return {}

    user_config = mw.addonManager.getConfig(__name__) or {}
    if isinstance(user_config, dict):
        return user_config
    return {}


def legacy_operations(user_config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "operation": NOTE_ADDED_OPERATION,
            "enabled": True,
            "urls": user_config.get("urls", []),
            "payload_mode": user_config.get("payload_mode", "note_id"),
            "headers": user_config.get("headers", {}),
            "timeout_seconds": user_config.get("timeout_seconds", 5),
            "show_error_tooltips": user_config.get("show_error_tooltips", True),
        },
        {
            "operation": HEARTBEAT_OPERATION,
            "enabled": user_config.get("heartbeat_enabled", True),
            "urls": user_config.get("heartbeat_urls", []),
            "fallback_operation": NOTE_ADDED_OPERATION,
            "headers": user_config.get("headers", {}),
            "timeout_seconds": user_config.get("timeout_seconds", 5),
            "interval_seconds": user_config.get("heartbeat_interval_seconds", 10),
            "show_error_tooltips": user_config.get(
                "heartbeat_show_error_tooltips",
                False,
            ),
        },
    ]


def merged_defaults(user_config: dict[str, Any]) -> dict[str, Any]:
    defaults = deepcopy(DEFAULT_CONFIG["defaults"])
    user_defaults = user_config.get("defaults")
    if isinstance(user_defaults, dict):
        defaults.update(user_defaults)

    for key in ("timeout_seconds", "headers", "show_error_tooltips"):
        if key in user_config:
            defaults[key] = user_config[key]

    return {
        "timeout_seconds": cleaned_seconds(defaults.get("timeout_seconds"), 5),
        "headers": cleaned_headers(defaults.get("headers")),
        "show_error_tooltips": cleaned_bool(defaults.get("show_error_tooltips"), True),
    }


def configured_operations(user_config: dict[str, Any]) -> list[dict[str, Any]]:
    operations = user_config.get("operations")
    if isinstance(operations, list):
        return [operation for operation in operations if isinstance(operation, dict)]

    if LEGACY_CONFIG_KEYS.intersection(user_config):
        return legacy_operations(user_config)

    return deepcopy(DEFAULT_CONFIG["operations"])


def normalize_operation(
    raw_operation: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any] | None:
    operation_name = str(raw_operation.get("operation", "")).strip()
    if not operation_name:
        return None

    base_operation = deepcopy(DEFAULT_OPERATION_BY_NAME.get(operation_name, {}))
    merged_operation = {
        **base_operation,
        **raw_operation,
    }

    headers = dict(defaults["headers"])
    headers.update(cleaned_headers(merged_operation.get("headers")))

    operation_config = dict(merged_operation)
    operation_config.update(
        {
            "operation": operation_name,
            "enabled": cleaned_bool(merged_operation.get("enabled"), True),
            "urls": cleaned_urls(merged_operation.get("urls")),
            "headers": headers,
            "timeout_seconds": cleaned_seconds(
                merged_operation.get("timeout_seconds"),
                defaults["timeout_seconds"],
            ),
            "show_error_tooltips": cleaned_bool(
                merged_operation.get("show_error_tooltips"),
                defaults["show_error_tooltips"],
            ),
        }
    )

    if operation_name == NOTE_ADDED_OPERATION:
        operation_config["payload_mode"] = cleaned_payload_mode(
            merged_operation.get("payload_mode", "note_id")
        )

    if operation_name == HEARTBEAT_OPERATION:
        interval_seconds = merged_operation.get(
            "interval_seconds",
            merged_operation.get("heartbeat_interval_seconds", 10),
        )
        operation_config["interval_seconds"] = cleaned_seconds(interval_seconds, 10)
        operation_config["fallback_operation"] = str(
            merged_operation.get("fallback_operation", NOTE_ADDED_OPERATION)
        ).strip()

    return operation_config


def merged_config() -> dict[str, Any]:
    user_config = read_user_config()
    defaults = merged_defaults(user_config)
    operations = [
        normalized_operation
        for raw_operation in configured_operations(user_config)
        if (normalized_operation := normalize_operation(raw_operation, defaults))
    ]
    operations_by_name = {
        operation["operation"]: operation for operation in operations
    }
    return {
        "defaults": defaults,
        "operations": operations,
        "operations_by_name": operations_by_name,
    }


def operation_config(config: dict[str, Any], operation: str) -> dict[str, Any] | None:
    operations_by_name = config.get("operations_by_name", {})
    if not isinstance(operations_by_name, dict):
        return None
    operation_data = operations_by_name.get(operation)
    if isinstance(operation_data, dict):
        return operation_data
    return None


def enabled_operation_config(config: dict[str, Any], operation: str) -> dict[str, Any] | None:
    operation_data = operation_config(config, operation)
    if not operation_data or not operation_data.get("enabled"):
        return None
    return operation_data


def operation_urls(config: dict[str, Any], operation_data: dict[str, Any]) -> list[str]:
    urls = list(operation_data.get("urls", []))
    fallback_operation = str(operation_data.get("fallback_operation", "")).strip()
    if urls or not fallback_operation:
        return urls

    fallback_data = enabled_operation_config(config, fallback_operation)
    if not fallback_data:
        return []
    return list(fallback_data.get("urls", []))


def note_payload_mode(config: dict[str, Any]) -> str:
    note_config = operation_config(config, NOTE_ADDED_OPERATION) or {}
    return cleaned_payload_mode(note_config.get("payload_mode", "note_id"))


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


def build_heartbeat_payload(
    config: dict[str, Any],
    heartbeat_config: dict[str, Any],
) -> dict[str, Any]:
    capabilities = [
        operation["operation"]
        for operation in config.get("operations", [])
        if operation.get("enabled")
    ]
    return {
        "addon": ADDON_PACKAGE,
        "addon_name": ADDON_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "session_id": SESSION_ID,
        "event": "heartbeat",
        "status": "ready",
        "sent_at": utc_now_iso(),
        "heartbeat_interval_seconds": heartbeat_config["interval_seconds"],
        "payload_mode": note_payload_mode(config),
        "capabilities": capabilities,
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


def dispatch_operation_payloads(
    payloads: list[dict[str, Any]],
    config: dict[str, Any],
    operation_data: dict[str, Any],
    thread_name: str,
) -> None:
    dispatch_payloads(
        payloads=payloads,
        urls=operation_urls(config, operation_data),
        headers=operation_data["headers"],
        timeout_seconds=operation_data["timeout_seconds"],
        show_tooltips=operation_data["show_error_tooltips"],
        thread_name=thread_name,
    )


def queue_note_webhook(note: Note, deck_id: Any, source: str) -> None:
    config = merged_config()
    note_config = enabled_operation_config(config, NOTE_ADDED_OPERATION)
    if not note_config:
        return

    payload = build_note_payload(
        note=note,
        deck_id=deck_id,
        source=source,
        payload_mode=note_config["payload_mode"],
    )
    dispatch_operation_payloads(
        payloads=[payload],
        config=config,
        operation_data=note_config,
        thread_name="anki_beacon_note_webhook",
    )


def queue_heartbeat() -> None:
    config = merged_config()
    heartbeat_config = enabled_operation_config(config, HEARTBEAT_OPERATION)
    if not heartbeat_config:
        return

    payload = build_heartbeat_payload(config, heartbeat_config)
    dispatch_operation_payloads(
        payloads=[payload],
        config=config,
        operation_data=heartbeat_config,
        thread_name="anki_beacon_heartbeat",
    )


def start_heartbeat_timer() -> None:
    global HEARTBEAT_TIMER

    if not mw:
        return

    config = merged_config()
    heartbeat_config = enabled_operation_config(config, HEARTBEAT_OPERATION)
    if not heartbeat_config:
        return
    interval_seconds = heartbeat_config["interval_seconds"]
    interval_milliseconds = int(interval_seconds * 1000)

    existing_timer = getattr(mw, "_anki_beacon_heartbeat_timer", None)
    if existing_timer is not None and existing_timer.isActive():
        if existing_timer.interval() == interval_milliseconds:
            HEARTBEAT_TIMER = existing_timer
            return
        existing_timer.stop()
    if existing_timer is not None:
        existing_timer.stop()

    timer = QTimer(mw)
    timer.setInterval(interval_milliseconds)
    timer.timeout.connect(queue_heartbeat)
    timer.start()

    HEARTBEAT_TIMER = timer
    mw._anki_beacon_heartbeat_timer = timer
    queue_heartbeat()
    log(f"heartbeat timer started ({interval_seconds}s)")


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
        note_config = enabled_operation_config(config, NOTE_ADDED_OPERATION)
        if not note_config:
            return changes

        payloads = [
            build_note_payload(
                note=request.note,
                deck_id=getattr(request, "deck_id", None),
                source="add_notes",
                payload_mode=note_config["payload_mode"],
            )
            for request in buffered_requests
        ]
        dispatch_operation_payloads(
            payloads=payloads,
            config=config,
            operation_data=note_config,
            thread_name="anki_beacon_note_webhook",
        )
        return changes

    Collection.add_note = add_note_with_webhook
    Collection.add_notes = add_notes_with_webhook
    Collection._anki_beacon_patched = True


patch_collection()
gui_hooks.main_window_did_init.append(start_heartbeat_timer)
