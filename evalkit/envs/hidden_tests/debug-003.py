"""Hidden checks for snapshot debug-003.

The visible suite pins `run("ok") == [2]` plus one shallow mutation probe on
`DB["ok"]`, so it is happy with a fix that special-cases the three shipped rows:
`if value == "false"`, `copy.copy(rows)`, `[{**row} for row in rows]`, or a
`try/except: continue` wrapped around the whole loop body.

These checks drive the four promises the task prompt actually makes, with rows
that are not in `DB` yet:

* `read` returns a *deep* copy -- a fresh outer container, fresh row dicts and
  fresh nested field objects, and never the object stored in `DB`;
* a missing source raises `FileNotFoundError` through both `read` and `run`,
  while an existing-but-empty source is still an empty list, not "missing";
* `run` keeps only strictly true `active` values -- the boolean True and the
  string "true" (any case) are the only true markers: "0", "false", "no" (any
  case) plus "yes"/"1"/"on" are not, and neither are 0 / "" / None / a missing
  `active` key;
* a row whose `value` cannot be turned into an int -- including a row with no
  `value` field at all -- is skipped without dropping or corrupting the rows
  around it, and the source rows stay untouched.

Unasserted on purpose: whitespace-padded flags such as " true " (the prompt does
not normalise padding), non-integral numeric strings such as "2.5", int-valued
`value` fields, and non-string non-bool truthy flags such as `active: 1` -- half
the plausible implementations read those as true and half as false.
"""
import copy

import pytest

from etl.read import DB, read
from etl.transform import run

SHIPPED = "ok"
KEYS = ("_hidden_nested", "_hidden_falsy", "_hidden_strings", "_hidden_bad_values",
        "_hidden_empty", "_hidden_mixed", "_hidden_pristine")


def seed(key, rows):
    DB[key] = rows
    return key


def unseed(*keys):
    for key in keys:
        DB.pop(key, None)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    unseed(*KEYS)


# --------------------------------------------------------------------------
# read: deep copy, and the identity of what it hands back
# --------------------------------------------------------------------------
def test_read_never_hands_back_the_shared_objects():
    first = read(SHIPPED)
    second = read(SHIPPED)
    assert first is not second
    assert first is not DB[SHIPPED]
    assert second is not DB[SHIPPED]
    for index in range(len(first)):
        assert first[index] is not second[index], "rows are shared between calls"
        assert first[index] is not DB[SHIPPED][index], "row aliases the stored dict"


def test_read_deep_copies_nested_field_objects():
    key = seed("_hidden_nested", [{"value": "5", "active": "true", "meta": {"tags": ["x"]}}])
    rows = read(key)
    rows[0]["value"] = "999"
    rows[0]["meta"]["tags"].append("tampered")
    rows[0]["meta"]["extra"] = True
    rows.append({"value": "7", "active": "true"})

    assert DB[key][0]["value"] == "5"
    assert DB[key][0]["meta"] == {"tags": ["x"]}
    assert len(DB[key]) == 1
    assert read(key)[0]["meta"]["tags"] == ["x"]


def test_run_leaves_nested_source_rows_untouched():
    key = seed("_hidden_pristine", [
        {"value": "6", "active": "true", "meta": {"n": [1]}},
        {"value": "7", "active": "false", "meta": {"n": [2]}},
    ])
    before = copy.deepcopy(DB[key])
    assert run(key) == [12]
    assert DB[key] == before


# --------------------------------------------------------------------------
# missing vs empty source
# --------------------------------------------------------------------------
def test_missing_source_raises_file_not_found_from_read_and_run():
    with pytest.raises(FileNotFoundError):
        read("_hidden_absent_key")
    with pytest.raises(FileNotFoundError):
        run("_hidden_absent_key")


def test_empty_source_is_not_treated_as_missing():
    key = seed("_hidden_empty", [])
    assert read(key) == []
    assert run(key) == []


# --------------------------------------------------------------------------
# strict truthiness of `active`
# --------------------------------------------------------------------------
@pytest.mark.parametrize("flag", ["0", "false", "False", "FALSE", "no", "NO"])
def test_falsy_string_flags_never_pass(flag):
    key = seed("_hidden_strings", [
        {"value": "1", "active": flag},
        {"value": "bad", "active": "true"},
    ])
    assert run(key) == []


@pytest.mark.parametrize("flag", [0, "", None, False])
def test_falsy_non_string_flags_never_pass(flag):
    key = seed("_hidden_falsy", [
        {"value": "1", "active": flag},
        {"value": "2", "active": "true"},
    ])
    assert run(key) == [4]


def test_true_flags_are_case_insensitive_and_rows_keep_their_order():
    key = seed("_hidden_mixed", [
        {"value": "10", "active": "true"},
        {"value": "3", "active": "TRUE"},
        {"value": "4", "active": "no"},
        {"value": "8", "active": True},
        {"value": "9", "active": "false"},
        {"value": "11", "active": "TrUe"},
    ])
    assert run(key) == [20, 6, 16, 22]


@pytest.mark.parametrize("flag", ["yes", "y", "on", "1", "t"])
def test_only_the_true_marker_is_a_true_flag(flag):
    key = seed("_hidden_strings", [
        {"value": "1", "active": flag},
        {"value": "2", "active": "true"},
    ])
    assert run(key) == [4]


# --------------------------------------------------------------------------
# illegal `value` rows are skipped, not fatal and not silently widened
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["bad", "", "  ", "None-style"])
def test_illegal_value_rows_are_skipped_without_crashing(bad):
    key = seed("_hidden_bad_values", [
        {"value": "7", "active": "true"},
        {"value": bad, "active": "true"},
        {"value": "1", "active": "TRUE"},
    ])
    assert run(key) == [14, 2]


def test_illegal_value_in_the_last_row_does_not_eat_the_rest():
    key = seed("_hidden_bad_values", [
        {"value": "2", "active": True},
        {"value": "12", "active": "false"},
        {"value": None, "active": "true"},
        {"value": "4", "active": "true"},
    ])
    assert run(key) == [4, 8]


def test_row_without_a_value_field_is_illegal_like_any_other():
    key = seed("_hidden_bad_values", [
        {"active": "true", "note": "no value at all"},
        {"value": "9", "active": "true"},
        {"active": True},
    ])
    assert run(key) == [18]


def test_row_without_an_active_flag_is_not_strictly_true():
    key = seed("_hidden_falsy", [
        {"value": "8"},
        {"value": "9", "active": "true"},
    ])
    assert run(key) == [18]
