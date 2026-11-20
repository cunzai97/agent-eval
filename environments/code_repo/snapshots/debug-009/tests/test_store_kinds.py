"""Visible suite: happy paths for the kinds that are already supported."""
import pytest

from store import cli
from store.db import JsonStore
from store.portable import export_bundle, import_bundle, read_bundle, write_bundle
from store.render import render_card
from store.state import board_state, daemon_state

BASE_KINDS = ("issue", "agent", "squad")


def seed(store):
    agent = store.create("agent", {"name": "triage", "mode": "chat"})
    first = store.create("issue", {"title": "flaky export", "status": "open", "agent": agent["id"]})
    second = store.create("issue", {"title": "slow card", "status": "doing", "agent": agent["id"]})
    squad = store.create("squad", {"name": "oncall", "members": [agent["id"]]})
    return {"agent": agent, "issue": [first, second], "squad": squad}


def test_create_assigns_a_per_kind_sequence(tmp_path):
    store = JsonStore(tmp_path / "src")
    first = store.create("issue", {"title": "a"})
    second = store.create("issue", {"title": "b"})
    other = store.create("agent", {"name": "triage"})
    assert first["id"] != second["id"]
    assert other["kind"] == "agent"
    assert store.sequence("issue") == 2
    assert store.count("issue") == 2
    assert store.count("agent") == 1


def test_update_and_get_round_trip(tmp_path):
    store = JsonStore(tmp_path / "src")
    created = store.create("issue", {"title": "a", "status": "open"})
    updated = store.update("issue", created["id"], {"status": "done"})
    assert updated["status"] == "done"
    assert store.get("issue", created["id"])["status"] == "done"
    assert store.get("issue", "nope") is None


def test_list_returns_copies(tmp_path):
    store = JsonStore(tmp_path / "src")
    store.create("issue", {"title": "a"})
    items = store.list("issue")
    items[0]["title"] = "mutated"
    assert store.get("issue", items[0]["id"])["title"] == "a"


def test_unknown_kind_is_still_rejected(tmp_path):
    store = JsonStore(tmp_path / "src")
    with pytest.raises(ValueError):
        store.create("ghost", {"title": "boo"})


def test_export_writes_every_registered_section(tmp_path):
    store = JsonStore(tmp_path / "src")
    seed(store)
    bundle = export_bundle(store)
    assert bundle["format"] == "boardstore-bundle/1"
    for kind in BASE_KINDS:
        assert kind in bundle["sections"]
        assert bundle["sequences"][kind] == store.sequence(kind)
    assert len(bundle["sections"]["issue"]) == 2


def test_export_import_round_trip_preserves_records(tmp_path):
    source = JsonStore(tmp_path / "src")
    seed(source)
    bundle_path = tmp_path / "bundle.json"
    write_bundle(source, bundle_path)

    target = JsonStore(tmp_path / "dst")
    imported = import_bundle(target, read_bundle(bundle_path))
    assert sum(imported.values()) == 4
    for kind in BASE_KINDS:
        assert target.list(kind) == source.list(kind)
        assert target.sequence(kind) >= source.sequence(kind)


def test_import_carries_the_sequence_so_new_ids_stay_unique(tmp_path):
    source = JsonStore(tmp_path / "src")
    for index in range(3):
        source.create("issue", {"title": f"src-{index}"})
    source.create("agent", {"name": "triage"})

    target = JsonStore(tmp_path / "dst")
    import_bundle(target, export_bundle(source))
    fresh = target.create("issue", {"title": "after"})
    ids = [item["id"] for item in target.list("issue")]
    assert len(ids) == len(set(ids)) == 4
    assert fresh["id"] == "issue4"
    assert target.sequence("issue") >= 4
    assert target.count("agent") == 1


def test_state_payloads_report_registered_kinds(tmp_path):
    store = JsonStore(tmp_path / "src")
    seed(store)
    heartbeat = daemon_state(store)
    board = board_state(store)
    for kind in BASE_KINDS:
        assert heartbeat["counts"][kind] == store.count(kind)
        assert heartbeat["sequences"][kind] == store.sequence(kind)
    columns = {group["kind"]: group for group in board["groups"]}
    for kind in BASE_KINDS:
        assert columns[kind]["size"] == store.count(kind)
        assert columns[kind]["label"]
    assert board["total"] >= 4


def test_cards_and_cli_know_the_registered_kinds(tmp_path):
    store = JsonStore(tmp_path / "src")
    seed(store)
    for kind in BASE_KINDS:
        for item in store.list(kind):
            card = render_card(item)
            assert item["id"] in card
            assert "unsupported" not in card
        assert kind in cli.KIND_CHOICES
        assert kind in cli.usage_text()
