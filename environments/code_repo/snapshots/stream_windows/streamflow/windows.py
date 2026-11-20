"""Fixed-size event-time windows."""


def window_events(events, size):
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("size must be a positive integer")
    grouped = {}
    for event in events:
        start = (event["ts"] // size) * size
        grouped.setdefault(start, []).append(dict(event))
    return [
        {
            "start": start,
            "end": start + size - 1,
            "events": sorted(grouped[start], key=lambda event: event["id"]),
        }
        for start in sorted(grouped)
    ]
