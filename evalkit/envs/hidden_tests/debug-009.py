"""Hidden checks for snapshot debug-009.

The visible suite only walks the three kinds the store already supported, so it
stays green no matter how half-finished the registration of the new ``run``
kind is. These checks drive the paths that a half registered kind silently
skips: the export/import round trip, both state payloads, the renderer, the CLI
and the unknown-kind guards.
"""
import json
import os

import pytest

from store import cli
from store.db import JsonStore
from store.portable import export_bundle, import_bundle, read_bundle, write_bundle
from store.render import render_card
from store.state import board_state, daemon_state

BASE_KINDS = ("issue", "agent", "squad")


def make_run(store, name, status="queued", agent="agent1"):
    return store.create("run", {"name": name, "status": status, "agent": agent})


def populate(store):
    """Seed one record per base kind plus two runs."""
    store.create("issue", {"title": "keep me", "status": "open"})
    store.create("agent", {"name": "triage", "mode": "chat"})
    store.create("squad", {"name": "oncall", "members": ["agent1"]})
    first = make_run(store, "nightly")
    second = make_run(store, "smoke", status="done", agent="agent2")
    return first, second


def round_trip(tmp_path, source, name="bundle.json"):
    """export -> file -> read -> import into a fresh store -> read back."""
    bundle_path = tmp_path / name
    written = write_bundle(source, bundle_path)
    on_disk = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert on_disk == written
    target = JsonStore(tmp_path / "imported-into")
    imported = import_bundle(target, read_bundle(bundle_path))
    return target, imported


def test_run_records_are_stored_and_listed(tmp_path):
    store = JsonStore(tmp_path / "src")
    first = make_run(store, "nightly")
    second = make_run(store, "smoke", status="done")

    assert first["kind"] == "run"
    assert store.get("run", first["id"])["name"] == "nightly"
    updated = store.update("run", second["id"], {"status": "failed"})
    assert updated["status"] == "failed"
    assert [item["id"] for item in store.list("run")] == [first["id"], second["id"]]
    assert store.count("run") == 2
    assert store.counts()["run"] == 2


def test_run_survives_an_export_import_round_trip(tmp_path):
    source = JsonStore(tmp_path / "src")
    seeded = populate(source)

    target, imported = round_trip(tmp_path, source)

    assert target.list("run") == source.list("run")
    for item in seeded:
        restored = target.get("run", item["id"])
        assert restored is not None, f"{item['id']} vanished from the round trip"
        assert restored["name"] == item["name"]
        assert restored["status"] == item["status"]
        assert restored["agent"] == item["agent"]
        assert restored["kind"] == "run"
    assert imported.get("run") == 2


def test_run_sequence_survives_the_round_trip(tmp_path):
    source = JsonStore(tmp_path / "src")
    for index in range(4):
        make_run(source, f"job-{index}")
    source_ids = [item["id"] for item in source.list("run")]

    target, _ = round_trip(tmp_path, source, name="seq.json")
    fresh = target.create("run", {"name": "after-import"})

    ids = source_ids + [fresh["id"]]
    assert len(ids) == len(set(ids)), f"imported run ids collide with {fresh['id']}"
    assert target.count("run") == 5
    assert target.sequence("run") >= 5


def test_existing_kinds_still_round_trip_once_run_is_registered(tmp_path):
    source = JsonStore(tmp_path / "src")
    populate(source)
    for index in range(2):
        source.create("issue", {"title": f"extra-{index}", "status": "open"})

    target, _ = round_trip(tmp_path, source, name="all.json")

    for kind in BASE_KINDS:
        assert target.list(kind) == source.list(kind), f"{kind} records were lost"
        assert target.sequence(kind) >= source.sequence(kind)
    assert target.count("issue") == 3


def test_run_is_reported_in_both_state_payloads(tmp_path):
    store = JsonStore(tmp_path / "src")
    populate(store)

    heartbeat = daemon_state(store)
    assert heartbeat["counts"]["run"] == 2
    assert heartbeat["sequences"]["run"] == store.sequence("run")

    columns = {group["kind"]: group for group in board_state(store)["groups"]}
    assert "run" in columns, "board payload has no run column"
    assert columns["run"]["size"] == 2
    assert columns["run"]["label"]
    assert [item["name"] for item in columns["run"]["items"]] == ["nightly", "smoke"]


def test_run_renders_and_the_cli_accepts_it(tmp_path):
    store = JsonStore(tmp_path / "src")
    created = make_run(store, "nightly", status="running")
    card = render_card(store.get("run", created["id"]))

    assert "nightly" in card
    assert "unsupported" not in card
    assert "running" in card

    assert "run" in cli.KIND_CHOICES
    assert "run" in cli.usage_text()


def test_guards_against_a_permissive_registration(tmp_path):
    store = JsonStore(tmp_path / "src")
    populate(store)
    with pytest.raises(ValueError):
        store.create("ghost", {"name": "boo"})
    with pytest.raises(ValueError):
        store.list("ghost")

    bundle = export_bundle(store)
    bundle["sections"]["ghost"] = [{"id": "ghost1", "kind": "ghost"}]

    target = JsonStore(tmp_path / "later")
    import_bundle(target, bundle)

    assert target.list("run") == store.list("run"), "an unknown section broke the merge"
    data_dir = getattr(target, "data_dir", os.path.join(target.root, "data"))
    leftovers = [name for name in os.listdir(data_dir) if "ghost" in name]
    assert leftovers == []
