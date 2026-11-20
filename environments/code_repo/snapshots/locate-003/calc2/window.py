from datetime import datetime, timedelta, timezone

from .policy import inside_window


def recent(records, now, hours):
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=hours)
    return [
        record for record in records
        if inside_window(datetime.fromisoformat(record["ts"].replace("Z", "+00:00")), cutoff)
    ]
