import math
from collections import UserDict

import pytest

from streamflow import normalize_events, summarize, window_events


def event(id="a", ts=0, user="u", value=1):
    return {"id": id, "ts": ts, "user": user, "value": value}


def test_mapping_input_and_extra_fields():
    raw = event()
    raw["extra"] = "ignored"
    rows = normalize_events([UserDict(raw)])
    assert rows == [{"id": "a", "ts": 0, "user": "u", "value": 1.0}]


@pytest.mark.parametrize("record", [None, [], "event"])
def test_non_mapping_rejected(record):
    with pytest.raises(ValueError):
        normalize_events([record])


def test_missing_field_rejected():
    with pytest.raises(ValueError):
        normalize_events([{"id": "a", "ts": 0, "user": "u"}])


@pytest.mark.parametrize("field", ["id", "user"])
def test_blank_strings_rejected(field):
    with pytest.raises(ValueError):
        normalize_events([event(**{field: "  "})])


@pytest.mark.parametrize("ts", [True, False, -1, 1.5, "1"])
def test_bad_timestamps_rejected(ts):
    with pytest.raises(ValueError):
        normalize_events([event(ts=ts)])


@pytest.mark.parametrize("value", [True, False, "1", None])
def test_bad_value_types_rejected(value):
    with pytest.raises(ValueError):
        normalize_events([event(value=value)])


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_values_rejected(value):
    with pytest.raises(ValueError):
        normalize_events([event(value=value)])


def test_last_duplicate_wins_before_sorting():
    rows = normalize_events([
        event("x", 20, value=1), event("a", 8), event("x", 3, value=9),
    ])
    assert [(row["id"], row["ts"], row["value"]) for row in rows] == [("x", 3, 9.0), ("a", 8, 1.0)]


def test_normalize_does_not_alias_input():
    raw = event()
    rows = normalize_events([raw])
    rows[0]["user"] = "changed"
    assert raw["user"] == "u"


def test_empty_windows_and_summary():
    assert window_events([], 10) == []
    assert summarize([], 10) == []


def test_multiple_windows_are_sparse_and_ordered():
    rows = window_events([event("a", 25), event("b", 1), event("c", 12)], 10)
    assert [(row["start"], row["end"]) for row in rows] == [(0, 10), (10, 20), (20, 30)]
    assert [row["count"] if "count" in row else len(row["events"]) for row in rows] == [1, 1, 1]


def test_exact_boundary_belongs_to_next_window():
    rows = window_events([event("a", 9), event("b", 10)], 10)
    assert [len(row["events"]) for row in rows] == [1, 1]
    assert rows[1]["start"] == 10


def test_event_order_is_timestamp_then_id():
    rows = window_events([event("b", 2), event("a", 2), event("z", 1)], 10)
    assert [row["id"] for row in rows[0]["events"]] == ["z", "a", "b"]


def test_window_copies_events():
    original = event()
    rows = window_events([original], 10)
    rows[0]["events"][0]["user"] = "changed"
    assert original["user"] == "u"


def test_summary_unique_users_and_negative_values():
    rows = summarize([event("a", 1, "u", -2), event("b", 2, "u", 1), event("c", 3, "v", 4)], 10)
    assert rows == [{"start": 0, "end": 10, "count": 3, "total": 3.0, "users": 2, "mean": 1.0}]


def test_summary_uses_last_duplicate():
    rows = summarize([event("a", 1, "old", 100), event("a", 2, "new", 2)], 10)
    assert rows[0]["count"] == 1
    assert rows[0]["users"] == 1
    assert rows[0]["total"] == 2.0


def test_summary_multiple_windows():
    rows = summarize([event("a", 1, value=2), event("b", 11, value=4)], 10)
    assert [(row["start"], row["total"], row["mean"]) for row in rows] == [(0, 2.0, 2.0), (10, 4.0, 4.0)]


@pytest.mark.parametrize("size", [0, -2, 1.5, True, "10"])
def test_summary_rejects_bad_size(size):
    with pytest.raises(ValueError):
        summarize([], size)
