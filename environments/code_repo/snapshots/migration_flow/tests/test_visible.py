import hashlib

import pytest

from migratekit.registry import discover
from migratekit.runner import ChecksumMismatch, apply, checksum, plan


def test_discover_orders_by_numeric_version():
    assert discover(["0010_ten.sql", "0002_two.sql"]) == [(2, "0002_two.sql"), (10, "0010_ten.sql")]


def test_discover_rejects_bad_name():
    with pytest.raises(ValueError):
        discover(["add_users.sql"])


def test_discover_rejects_duplicate_version():
    with pytest.raises(ValueError):
        discover(["0001_a.sql", "1_b.sql"])


def test_checksum_uses_content():
    assert checksum("SELECT 1;") == hashlib.sha256(b"SELECT 1;").hexdigest()


def test_plan_skips_applied():
    migrations = [(1, "0001_a.sql"), (2, "0002_b.sql")]
    assert plan({"0001_a.sql": "x"}, migrations) == [(2, "0002_b.sql")]


def test_apply_records_checksums_and_skips_rerun():
    state = {}
    migrations = [(1, "0001_a.sql")]
    files = {"0001_a.sql": "CREATE TABLE a;"}
    first = apply(state, migrations, files.__getitem__, lambda sql: None)
    assert first == {"applied": ["0001_a.sql"], "skipped": [], "failed": None}
    assert state["applied"] == {"0001_a.sql": checksum("CREATE TABLE a;")}
    second = apply(state, migrations, files.__getitem__, lambda sql: None)
    assert second["skipped"] == ["0001_a.sql"]


def test_apply_stops_on_failure():
    state = {}
    migrations = [(1, "0001_a.sql"), (2, "0002_b.sql")]
    files = {"0001_a.sql": "a", "0002_b.sql": "b"}

    def boom(sql):
        raise RuntimeError("nope")

    report = apply(state, migrations, files.__getitem__, boom)
    assert report["failed"] == "0001_a.sql"
    assert state.get("applied") == {}


def test_apply_rejects_changed_checksum():
    state = {"applied": {"0001_a.sql": "stale"}}
    with pytest.raises(ChecksumMismatch):
        apply(state, [(1, "0001_a.sql")], {"0001_a.sql": "new"}.__getitem__, lambda sql: None)
