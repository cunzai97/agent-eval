from .rate import success_rate


def report(events):
    by_group = {}
    for event in events:
        by_group.setdefault(event.get("group", "unknown"), []).append(event)
    return {
        "overall": round(success_rate(events) * 10, 1),
        "groups": {name: round(success_rate(rows) * 10, 1) for name, rows in sorted(by_group.items())},
    }
