from agg.rate import success_rate
from agg.report import report


EVENTS = [
    {"group": "a", "ok": True},
    {"group": "a", "ok": "false"},
    {"group": "a", "ok": False, "status": "ignored"},
    {"group": "b", "ok": "TRUE"},
]


def test_string_false_is_false():
    assert success_rate([{"ok": "false"}]) == 0.0


def test_case_insensitive_true_string():
    assert success_rate([{"ok": "TRUE"}]) == 1.0


def test_ignored_events_are_excluded_from_denominator():
    assert success_rate(EVENTS) == 2 / 3


def test_empty_rate():
    assert success_rate([]) == 0.0


def test_report_scales_to_percentage():
    assert report([{"ok": True}, {"ok": False}])["overall"] == 50.0


def test_group_rates_have_independent_denominators():
    assert report(EVENTS)["groups"] == {"a": 50.0, "b": 100.0}


def test_missing_group_is_named_unknown():
    assert report([{"ok": True}])["groups"] == {"unknown": 100.0}


def test_report_rounds_to_one_decimal():
    assert report([{"ok": True}, {"ok": True}, {"ok": False}])["overall"] == 66.7
