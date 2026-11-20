from datetime import datetime, timezone

from calc2.service import recent_average


NOW = datetime(2025, 2, 1, 0, 0, tzinfo=timezone.utc)


def test_inside_window():
    assert recent_average([{"ts": "2025-01-31T12:00:00Z", "value": 4}], NOW) == 4


def test_exact_cutoff_is_inclusive():
    rows = [
        {"ts": "2025-01-31T00:00:00Z", "value": 2},
        {"ts": "2025-01-31T12:00:00Z", "value": 4},
    ]
    assert recent_average(rows, NOW) == 3


def test_timezone_offset_is_normalized():
    assert recent_average([{"ts": "2025-01-31T20:00:00+08:00", "value": 7}], NOW) == 7
