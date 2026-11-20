"""Hidden checks for snapshot debug-004.

The report is silent (no traceback), and the visible suite pins one shape per
bug: `success_rate([{"ok": "false"}]) == 0.0`, one 3-event denominator, one 50%
scale and one 2-group report.  A fix that patches those exact inputs -- an
`if value == "false"` clause, `* 10` swapped for `* 100` while the denominator is
still wrong, or a per-group rate divided by the global count -- keeps it green.

These checks re-drive the two layers the prompt names, on data the visible suite
never contains:

* string booleans: the boolean True and the string "true" (any case) are the
  only success markers, so "false"/"no"/"0"/"yes"/"1"/"ok" are all failures,
  while non-string values fall back to plain Python truthiness (1 yes, 0/""/None
  no); several of them are mixed into one denominator;
* `ignored` rows removed from the denominator only -- they still count as rows,
  so a group made only of ignored rows reports 0.0 instead of disappearing, and
  any other status ("pending", or no status at all) stays in the denominator;
* percentage scale: 3/8 -> 37.5, 5/6 -> 83.3, 7/8 -> 87.5, never 0.5 or 5000;
* per-group numerators *and* denominators, so each group's rate is its own;
* one-decimal rounding preserved on both `overall` and every group value;
* a zero denominator (no events at all, or nothing left after `ignored`) returns
  0.0 instead of raising ZeroDivisionError.

Unasserted on purpose: case or whitespace variants of the `status` marker, a
missing `ok` key, half-way rounding (a 6.25% tie rounding up or down), and any
output key beyond `overall`/`groups` -- the prompt does not define those.
"""
import pytest

from agg.rate import success_rate
from agg.report import report


# --------------------------------------------------------------------------
# string booleans
# --------------------------------------------------------------------------
@pytest.mark.parametrize("truthy", [True, "true", "TRUE", "True", "TrUe"])
def test_true_spellings_are_successes(truthy):
    assert success_rate([{"ok": "false"}, {"ok": truthy}]) == 0.5


@pytest.mark.parametrize("falsy",
                         [False, "false", "FALSE", "False", "no", "NO", "0", 0, "", None])
def test_false_spellings_are_not_successes(falsy):
    assert success_rate([{"ok": "true"}, {"ok": "true"}, {"ok": falsy}]) == pytest.approx(2 / 3)


def test_string_boolean_forms_mixed_in_one_denominator():
    events = [
        {"ok": "true"}, {"ok": "false"}, {"ok": "0"}, {"ok": "TRUE"},
        {"ok": "no"}, {"ok": True}, {"ok": None}, {"ok": "False"},
    ]
    assert success_rate(events) == pytest.approx(3 / 8)


@pytest.mark.parametrize("token", ["yes", "y", "on", "t", "1", "ok"])
def test_only_the_true_string_marker_is_a_success(token):
    assert success_rate([{"ok": "true"}, {"ok": token}]) == 0.5


def test_non_string_ok_values_fall_back_to_plain_truthiness():
    assert success_rate([{"ok": 1}, {"ok": 2}, {"ok": "true"}]) == 1.0
    assert success_rate([{"ok": 1}, {"ok": 0}]) == 0.5
    assert success_rate([{"ok": 3}, {"ok": "1"}]) == pytest.approx(1 / 2)


# --------------------------------------------------------------------------
# ignored: out of the denominator, still a row
# --------------------------------------------------------------------------
def test_ignored_rows_leave_the_denominator_only():
    events = [
        {"ok": True},
        {"ok": "false"},
        {"ok": True, "status": "ignored"},
        {"ok": True, "status": "ignored"},
        {"ok": "TRUE"},
        {"ok": True},
    ]
    assert success_rate(events) == pytest.approx(3 / 4)


def test_other_statuses_and_missing_status_stay_in_the_denominator():
    events = [
        {"ok": True, "status": "pending"},
        {"ok": False, "status": "pending"},
        {"ok": True},
        {"ok": False, "status": "ignored"},
    ]
    assert success_rate(events) == pytest.approx(2 / 3)


def test_no_events_and_all_ignored_both_report_zero():
    assert success_rate([]) == 0.0
    assert success_rate([{"ok": True, "status": "ignored"}]) == 0.0
    assert success_rate([{"ok": "true", "status": "ignored"},
                         {"ok": False, "status": "ignored"}]) == 0.0


# --------------------------------------------------------------------------
# percentage scale + one decimal
# --------------------------------------------------------------------------
@pytest.mark.parametrize("events, expected", [
    ([{"ok": True}], 100.0),
    ([{"ok": False}], 0.0),
    ([{"ok": True}, {"ok": False}, {"ok": False}], 33.3),
    ([{"ok": True}] * 3 + [{"ok": "false"}] * 5, 37.5),
    ([{"ok": True}] * 5 + [{"ok": "false"}], 83.3),
    ([{"ok": "no"}, {"ok": "no"}, {"ok": "no"}, {"ok": True}], 25.0),
])
def test_overall_is_a_percentage_with_one_decimal(events, expected):
    value = report(events)["overall"]
    assert value == expected
    assert value == round(value, 1)


# --------------------------------------------------------------------------
# per-group numerators and denominators
# --------------------------------------------------------------------------
def test_each_group_uses_its_own_denominator():
    events = [
        {"group": "a", "ok": True},
        {"group": "a", "ok": False},
        {"group": "b", "ok": True, "status": "ignored"},
        {"group": "c", "ok": True},
        {"group": "c", "ok": "true"},
        {"group": "c", "ok": "false"},
    ]
    result = report(events)
    assert result["groups"] == {"a": 50.0, "b": 0.0, "c": 66.7}
    assert result["overall"] == 60.0


def test_ignored_only_group_is_present_with_a_zero_rate():
    events = [
        {"group": "solo", "ok": True, "status": "ignored"},
        {"group": "team", "ok": True},
        {"group": "team", "ok": False},
    ]
    result = report(events)
    assert set(result["groups"]) == {"solo", "team"}
    assert result["groups"]["solo"] == 0.0
    assert result["groups"]["team"] == 50.0
    assert result["overall"] == 50.0


def test_many_groups_report_independently_and_round_per_group():
    events = (
        [{"group": "x", "ok": True}]
        + [{"group": "x", "ok": "false"}] * 2
        + [{"group": "y", "ok": "TRUE"}] * 7
        + [{"group": "y", "ok": False}]
        + [{"group": "z", "ok": False, "status": "ignored"}] * 3
        + [{"group": "z", "ok": True}]
    )
    result = report(events)
    assert result["groups"] == {"x": 33.3, "y": 87.5, "z": 100.0}
    assert result["overall"] == 75.0
    for name, value in result["groups"].items():
        assert value == round(value, 1), f"{name} lost the one-decimal contract"


def test_report_structure_and_unknown_group_survive_the_repairs():
    events = [{"group": "solo", "ok": True}, {"ok": "false"}]
    result = report(events)
    assert "overall" in result and "groups" in result
    assert result["groups"]["unknown"] == 0.0
    assert result["groups"]["solo"] == 100.0
    assert result["overall"] == 50.0
