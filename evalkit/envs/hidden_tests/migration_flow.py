"""Hidden checks for migration_flow: discovery, planning, idempotency and failure handling."""
import hashlib

import pytest

from migratekit.registry import discover
from migratekit.runner import ChecksumMismatch, apply, checksum, plan


def test_discover_sorts_numerically_not_lexicographically():
    files = ["0009_i.sql", "0010_j.sql", "0002_b.sql", "0001_a.sql"]
    assert discover(files) == [
        (1, "0001_a.sql"),
        (2, "0002_b.sql"),
        (9, "0009_i.sql"),
        (10, "0010_j.sql"),
    ]


def test_discover_compares_versions_as_integers():
    assert discover(["10_ten.sql", "2_two.sql"]) == [(2, "2_two.sql"), (10, "10_ten.sql")]


def test_discover_strips_directories():
    assert discover(["migrations/0002_b.sql", "migrations/0001_a.sql"]) == [
        (1, "0001_a.sql"),
        (2, "0002_b.sql"),
    ]


def test_discover_accepts_windows_separators():
    assert discover(["migrations\\0002_b.sql"]) == [(2, "0002_b.sql")]


@pytest.mark.parametrize(
    "name",
    [
        "add_users.sql",
        "0001-add.sql",
        "0001_Add.sql",
        "0001_a.txt",
        "0001_.sql",
        "0001_a.sql.bak",
        "",
    ],
)
def test_discover_rejects_invalid_names(name):
    with pytest.raises(ValueError):
        discover([name])


@pytest.mark.parametrize(
    "files",
    [
        ["0001_a.sql", "0001_b.sql"],
        ["1_a.sql", "0001_b.sql"],
        ["0003_a.sql", "0003_a.sql"],
    ],
)
def test_discover_rejects_duplicate_versions(files):
    with pytest.raises(ValueError):
        discover(files)


def test_discover_empty():
    assert discover([]) == []


@pytest.mark.parametrize(
    "sql",
    ["", "SELECT 1;", "CREATE TABLE t (id INT);\n", "插入一条记录"],
)
def test_checksum_matches_sha256(sql):
    assert checksum(sql) == hashlib.sha256(sql.encode("utf-8")).hexdigest()


def test_plan_keeps_order_and_skips_applied():
    migrations = [(1, "0001_a.sql"), (2, "0002_b.sql"), (3, "0003_c.sql")]
    assert plan({"0001_a.sql": "x", "0003_c.sql": "y"}, migrations) == [(2, "0002_b.sql")]


def test_plan_ignores_unknown_applied_entries():
    migrations = [(1, "0001_a.sql")]
    assert plan({"9999_gone.sql": "x"}, migrations) == migrations


def test_plan_returns_all_when_nothing_applied():
    migrations = [(2, "0002_b.sql"), (1, "0001_a.sql")]
    assert plan({}, migrations) == migrations


def test_apply_runs_in_given_order():
    state = {}
    executed = []
    migrations = [(1, "0001_a.sql"), (2, "0002_b.sql")]
    files = {"0001_a.sql": "a", "0002_b.sql": "b"}
    report = apply(state, migrations, files.__getitem__, executed.append)
    assert executed == ["a", "b"]
    assert report["applied"] == ["0001_a.sql", "0002_b.sql"]
    assert report["skipped"] == []
    assert report["failed"] is None


def test_apply_records_checksums_in_state():
    state = {}
    migrations = [(1, "0001_a.sql"), (2, "0002_b.sql")]
    files = {"0001_a.sql": "a", "0002_b.sql": "b"}
    apply(state, migrations, files.__getitem__, lambda sql: None)
    assert state["applied"] == {
        "0001_a.sql": checksum("a"),
        "0002_b.sql": checksum("b"),
    }


def test_apply_is_idempotent_on_rerun():
    state = {}
    migrations = [(1, "0001_a.sql")]
    files = {"0001_a.sql": "a"}
    executed = []
    apply(state, migrations, files.__getitem__, executed.append)
    report = apply(state, migrations, files.__getitem__, executed.append)
    assert executed == ["a"]
    assert report == {"applied": [], "skipped": ["0001_a.sql"], "failed": None}


def test_apply_stops_after_failure():
    state = {}
    migrations = [(1, "0001_a.sql"), (2, "0002_b.sql"), (3, "0003_c.sql")]
    files = {"0001_a.sql": "a", "0002_b.sql": "b", "0003_c.sql": "c"}
    executed = []

    def execute(sql):
        if sql == "b":
            raise RuntimeError("boom")
        executed.append(sql)

    report = apply(state, migrations, files.__getitem__, execute)
    assert executed == ["a"]
    assert report["applied"] == ["0001_a.sql"]
    assert report["failed"] == "0002_b.sql"
    assert "0002_b.sql" not in state["applied"]
    assert "0003_c.sql" not in state["applied"]


def test_apply_keeps_previous_checksums_after_failure():
    state = {"applied": {"0000_init.sql": "seed"}}
    migrations = [(1, "0001_a.sql")]
    files = {"0001_a.sql": "a"}

    def execute(sql):
        raise RuntimeError("boom")

    apply(state, migrations, files.__getitem__, execute)
    assert state["applied"] == {"0000_init.sql": "seed"}


@pytest.mark.parametrize("stale", ["", "not-a-digest", "0" * 64])
def test_apply_rejects_changed_content_without_executing(stale):
    state = {"applied": {"0001_a.sql": stale}}
    executed = []
    with pytest.raises(ChecksumMismatch):
        apply(state, [(1, "0001_a.sql")], {"0001_a.sql": "a"}.__getitem__, executed.append)
    assert executed == []


def test_apply_accepts_matching_checksum_without_executing():
    sql = "a"
    state = {"applied": {"0001_a.sql": checksum(sql)}}
    executed = []
    report = apply(state, [(1, "0001_a.sql")], {"0001_a.sql": sql}.__getitem__, executed.append)
    assert executed == []
    assert report == {"applied": [], "skipped": ["0001_a.sql"], "failed": None}


def test_apply_initialises_missing_state_key():
    state = {}
    apply(state, [(1, "0001_a.sql")], {"0001_a.sql": "a"}.__getitem__, lambda sql: None)
    assert "applied" in state


def test_apply_empty_migrations():
    state = {}
    assert apply(state, [], lambda name: "", lambda sql: None) == {
        "applied": [],
        "skipped": [],
        "failed": None,
    }


def test_apply_loads_by_name():
    state = {}
    seen = []

    def load(name):
        seen.append(name)
        return "content"

    apply(state, [(1, "0001_a.sql")], load, lambda sql: None)
    assert seen == ["0001_a.sql"]
