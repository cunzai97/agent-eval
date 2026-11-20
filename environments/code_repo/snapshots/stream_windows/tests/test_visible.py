import math

import pytest

from streamflow import normalize_events, summarize, window_events


def event(id="a", ts=0, user="u", value=1):
    return {"id": id, "ts": ts, "user": user, "value": value}


def test_normalize_strips_and_sorts():
    rows = normalize_events([event(" b ", 5, " alice "), event("a", 2, "bob", 2)])
    assert rows == [
        {"id": "a", "ts": 2, "user": "bob", "value": 2.0},
        {"id": "b", "ts": 5, "user": "alice", "value": 1.0},
    ]


def test_duplicate_id_last_record_wins():
    rows = normalize_events([event("x", 1, value=10), event("x", 9, value=20)])
    assert len(rows) == 1
    assert rows[0]["ts"] == 9
    assert rows[0]["value"] == 20.0


def test_non_finite_values_are_rejected():
    with pytest.raises(ValueError):
        normalize_events([event(value=math.nan)])


def test_window_end_is_exclusive():
    rows = window_events([event("a", 0), event("b", 9)], 10)
    assert rows[0]["start"] == 0
    assert rows[0]["end"] == 10


def test_window_events_use_timestamp_order():
    rows = window_events([event("z", 2), event("a", 8)], 10)
    assert [row["id"] for row in rows[0]["events"]] == ["z", "a"]


@pytest.mark.parametrize("size", [0, -1, True])
def test_invalid_window_size(size):
    with pytest.raises(ValueError):
        window_events([], size)


def test_summary_count_total_and_mean():
    rows = summarize([event("a", 1, "u1", 2), event("b", 2, "u2", 4)], 10)
    assert rows == [{"start": 0, "end": 10, "count": 2, "total": 6.0, "users": 2, "mean": 3.0}]


def test_summary_counts_unique_users():
    rows = summarize([event("a", 1, "u1", 2), event("b", 2, "u1", 4)], 10)
    assert rows[0]["users"] == 1
