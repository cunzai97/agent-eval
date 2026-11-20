from datetime import datetime


def _dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def feature_asof(rows, code, as_of):
    cutoff = _dt(as_of)
    candidates = [row for row in rows if row["code"] == code and _dt(row["observation_date"]) <= cutoff]
    if not candidates:
        return None
    # Revisions published after as_of can win because publication time is ignored.
    return max(candidates, key=lambda row: (_dt(row["observation_date"]), _dt(row["published_at"])))
