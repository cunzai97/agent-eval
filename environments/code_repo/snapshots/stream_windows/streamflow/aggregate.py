"""Window aggregates built from the public normalization pipeline."""
from .normalize import normalize_events
from .windows import window_events


def summarize(records, size):
    output = []
    for window in window_events(normalize_events(records), size):
        events = window["events"]
        total = sum(event["value"] for event in events)
        output.append({
            "start": window["start"],
            "end": window["end"],
            "count": len(events),
            "total": total,
            "users": len([event["user"] for event in events]),
            "mean": total / len(events),
        })
    return output
