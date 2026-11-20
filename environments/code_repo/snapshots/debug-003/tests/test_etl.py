import pytest

from etl.read import DB, read
from etl.transform import run


def test_transform_filters_inactive_and_skips_invalid_values():
    assert run("ok") == [2]


def test_repeated_run_is_stable():
    assert run("ok") == [2]
    assert run("ok") == [2]


def test_reader_returns_deep_copy():
    rows = read("ok")
    rows[0]["value"] = "999"
    assert read("ok")[0]["value"] == "1"


def test_transform_does_not_mutate_database():
    before = [dict(row) for row in DB["ok"]]
    run("ok")
    assert DB["ok"] == before


def test_missing_source_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        read("missing")


def test_boolean_active_is_supported():
    DB["temp"] = [{"value": "3", "active": True}, {"value": "4", "active": False}]
    try:
        assert run("temp") == [6]
    finally:
        del DB["temp"]


def test_case_insensitive_active_string():
    DB["temp"] = [{"value": "3", "active": "TRUE"}]
    try:
        assert run("temp") == [6]
    finally:
        del DB["temp"]


def test_empty_source():
    DB["temp"] = []
    try:
        assert run("temp") == []
    finally:
        del DB["temp"]
