"""Smoke suite for the audit trail: issues and agents.

These checks pin the trail *shape* and the attribution of the call sites that
were migrated when authentication landed. Scheduled runs are exercised for
their bookkeeping only.
"""
import pytest

from audit import log as audit_log
from background.runner import ScheduledJob, execute_job
from server.handlers import AuthenticationError, Principal, Request, dispatch
from tracker.workspace import Workspace

ENTITY_TYPES = {"issue", "agent", "squad", "run"}


def ws(name="home-a"):
    return Workspace(name)


def call(workspace, path, body=None, user="u-1", method="POST"):
    principal = Principal(user) if user else None
    return dispatch(Request(workspace, method, path, body, principal))


def since(mark):
    return audit_log.events()[mark:]


def mark():
    return len(audit_log.events())


def test_http_authentication_is_required():
    workspace = ws("home-auth")
    with pytest.raises(AuthenticationError):
        call(workspace, "/issues", {"title": "x", "reporter": "u-9"}, user=None)
    assert since(mark()) == []


def test_migrated_issue_paths_record_the_principal():
    workspace = ws("home-migrated")
    started = mark()
    created = call(workspace, "/issues", {"title": "no trail", "reporter": "u-1"}, user="u-1")
    commented = call(
        workspace, "/issues/comment", {"issue": created["id"], "body": "repro on 2 runs"}, user="u-2"
    )
    reopened = call(workspace, "/issues/reopen", {"issue": created["id"]}, user="u-3")

    assert commented["comments"] == 1
    assert reopened["status"] == "open"
    recorded = since(started)
    assert [(event.entity_type, event.action, event.actor) for event in recorded] == [
        ("issue", "create", "u-1"),
        ("issue", "comment", "u-2"),
        ("issue", "reopen", "u-3"),
    ]


def test_migrated_agent_paths_record_the_principal():
    workspace = ws("home-agents")
    started = mark()
    agent = call(workspace, "/agents", {"name": "triage", "owner": "u-4"}, user="u-4")
    renamed = call(workspace, "/agents/rename", {"agent": agent["id"], "name": "triage-v2"}, user="u-5")
    budgeted = call(workspace, "/agents/budget", {"agent": agent["id"], "budget": 40}, user="u-6")

    assert renamed["name"] == "triage-v2"
    assert budgeted["budget"] == 40
    assert [(event.action, event.actor) for event in since(started)] == [
        ("register", "u-4"),
        ("rename", "u-5"),
        ("set_budget", "u-6"),
    ]


def test_issue_assign_and_close_still_write_a_trail():
    workspace = ws("home-shape")
    created = call(workspace, "/issues", {"title": "shape", "reporter": "u-1"}, user="u-1")
    started = mark()
    assigned = call(
        workspace, "/issues/assign", {"issue": created["id"], "assignee": "u-7"}, user="u-7"
    )
    closed = call(
        workspace, "/issues/close", {"issue": created["id"], "resolution": "done"}, user="u-8"
    )

    assert assigned["assignee"] == "u-7"
    assert closed["status"] == "closed"
    recorded = since(started)
    assert [event.action for event in recorded] == ["assign", "close"]
    assert [event.entity_type for event in recorded] == ["issue", "issue"]
    assert [event.entity_id for event in recorded] == [created["id"]] * 2


def test_squad_membership_records_a_trail():
    workspace = ws("home-squad-shape")
    squad = call(workspace, "/squads", {"name": "oncall"}, user="u-1")
    started = mark()
    added = call(
        workspace, "/squads/members", {"squad": squad["id"], "member": "u-2", "role": "dev"}, user="u-2"
    )
    removed = call(
        workspace, "/squads/members/remove", {"squad": squad["id"], "member": "u-2"}, user="u-3"
    )
    assert added["members"] == ["u-2"]
    assert removed["members"] == []
    assert [event.action for event in since(started)] == ["add_member", "remove_member"]


def test_scheduled_run_bookkeeping():
    workspace = ws("home-scheduled")
    agent = call(workspace, "/agents", {"name": "nightly", "owner": "u-1"}, user="u-1")
    started = mark()
    job = ScheduledJob(workspace, agent["id"], "summarise yesterday", "u-9", steps=4, tool_calls=6)
    run = execute_job(job)

    assert run["status"] == "finished"
    assert run["steps"] == 4
    assert run["tool_calls"] == 6
    assert run["agent"] == agent["id"]
    assert run["source"] == "scheduler"
    assert [event.action for event in since(started)] == ["start", "progress", "finish"]


def test_run_lifecycle_through_http_returns_the_run():
    workspace = ws("home-run-shape")
    agent = call(workspace, "/agents", {"name": "worker", "owner": "u-1"}, user="u-1")
    started = mark()
    started_run = call(workspace, "/runs/start", {"agent": agent["id"], "steps": 2, "prompt": "go"}, user="u-2")
    progressed = call(
        workspace, "/runs/progress", {"run": started_run["id"], "steps": 3, "tool_calls": 5}, user="u-2"
    )
    finished = call(workspace, "/runs/finish", {"run": started_run["id"], "summary": "ok"}, user="u-2")

    assert progressed["steps"] == 5
    assert finished["status"] == "finished"
    assert {event.workspace for event in since(started)} == {"home-run-shape"}
    assert [event.entity_type for event in since(started)] == ["run"] * 4


def test_trail_rows_are_well_formed():
    workspace = ws("home-shape")
    started = mark()
    call(workspace, "/issues", {"title": "row", "reporter": "u-1"}, user="u-1")
    call(workspace, "/agents", {"name": "a", "owner": "u-1"}, user="u-1")
    recorded = since(started)

    assert len(recorded) == 2
    assert [event.seq for event in recorded] == list(range(recorded[0].seq, recorded[0].seq + 2))
    assert all(event.actor for event in recorded)
    assert all(isinstance(event.details, dict) for event in recorded)
    assert {event.entity_type for event in recorded} <= ENTITY_TYPES
    assert {event.workspace for event in recorded} == {"home-shape"}


def test_unknown_route_is_refused():
    workspace = ws("home-404")
    with pytest.raises(KeyError):
        call(workspace, "/issues/teleport", {})
