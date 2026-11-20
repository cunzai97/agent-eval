"""Event validation and deterministic de-duplication."""
import math


def _normalize(record):
    if not isinstance(record, dict):
        raise ValueError("event must be a mapping")
    missing = {"id", "ts", "user", "value"} - set(record)
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")
    event_id = record["id"].strip() if isinstance(record["id"], str) else ""
    user = record["user"].strip() if isinstance(record["user"], str) else ""
    if not event_id or not user:
        raise ValueError("id and user must be non-empty strings")
    ts = record["ts"]
    if not isinstance(ts, int) or ts < 0:
        raise ValueError("ts must be a non-negative integer")
    value = record["value"]
    if not isinstance(value, (int, float)):
        raise ValueError("value must be numeric")
    return {"id": event_id, "ts": ts, "user": user, "value": float(value)}


def normalize_events(records):
    """Validate records; duplicate ids currently keep the first record."""
    by_id = {}
    for record in records:
        event = _normalize(record)
        by_id.setdefault(event["id"], event)
    return sorted(by_id.values(), key=lambda event: (event["ts"], event["id"]))
