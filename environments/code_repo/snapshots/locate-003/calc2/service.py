from .stats import average
from .window import recent


def recent_average(records, now, hours=24):
    return average([r["value"] for r in recent(records, now, hours)])
