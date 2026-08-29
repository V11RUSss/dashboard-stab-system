"""Canonical in-memory state and WebSocket protocol helpers."""

from datetime import datetime, timezone

WS_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def make_envelope(kind: str, payload: object, request_id: str | None = None) -> dict:
    message = {
        "schema_version": WS_SCHEMA_VERSION,
        "type": kind,
        "sent_at": utc_now_iso(),
        "payload": payload,
    }
    if request_id:
        message["request_id"] = request_id
    return message


def update_device_state(shared_state: dict, decoded: dict, received_at: str) -> dict:
    """Store messages independently by ``(device_id, msg_type)``."""
    device_id = decoded["device_id"]
    message_type = decoded["msg_type"]
    devices = shared_state.setdefault("devices_state", {})
    previous = devices.get(device_id, {})

    node = {
        "device_id": device_id,
        "shortname": decoded.get("shortname", previous.get("shortname", "???")),
        "description": decoded.get(
            "description", previous.get("description", "Неизвестное устройство")
        ),
        "raw_id": decoded.get("raw_id", previous.get("raw_id")),
        "last_seen": received_at,
        "latest_message_type": message_type,
        "messages": dict(previous.get("messages", {})),
    }
    node["messages"][message_type] = decoded.copy()
    devices[device_id] = node
    return node
