def normalize_status(value):
    return bool(value)


def success_rate(events):
    valid = [event for event in events if event.get("status") != "ignored"]
    successes = sum(1 for event in valid if normalize_status(event.get("ok")))
    return successes / len(events) if events else 0.0
