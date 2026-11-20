"""Hidden checks for snapshot fix-009.

Authentication is real now, so every entry point knows its principal. The
visible suite is a smoke suite and stays green either way; these checks create
events for every entity kind and assert that the actor written to the trail is
the operator of that call, not the ``local-user`` default. Attribution that is
only correct for the kinds the smoke tests poke, or that leaks from one request
to the next, fails here.
"""
import pytest

from audit import log as audit_log
from background.runner import ScheduledJob, execute_job
from server.handlers import AuthenticationError, Principal, Request, dispatch
from tracker.workspace import Workspace

DEFAULT_ACTOR = "local-user"
KINDS = ("issue", "agent", "squad", "run")


def field(event, name):
    if isinstance(event, dict):
        return event[name]
    return getattr(event, name)


def mark():
    return len(audit_log.events())


def rows_since(start):
    return audit_log.events()[start:]


def trail_since(start):
    return [
        (field(e, "entity_type"), field(e, "entity_id"), field(e, "action"), field(e, "actor"))
        for e in rows_since(start)
    ]


def actors_for(start, entity_id, action=None):
    found = []
    for event in rows_since(start):
        if field(event, "entity_id") != entity_id:
            continue
        if action is not None and field(event, "action") != action:
            continue
        found.append(field(event, "actor"))
    return found


def call(workspace, path, body=None, user="u-0"):
    principal = Principal(user) if user else None
    return dispatch(Request(workspace, "POST", path, body, principal))


def test_every_issue_path_is_attributed_to_its_principal():
    workspace = Workspace("h-issues")
    start = mark()
    created = call(workspace, "/issues", {"title": "quiet trail", "reporter": "u-1"}, "u-1")
    issue_id = created["id"]
    call(workspace, "/issues/comment", {"issue": issue_id, "body": "repro"}, "u-2")
    call(workspace, "/issues/assign", {"issue": issue_id, "assignee": "u-3"}, "u-3")
    call(workspace, "/issues/close", {"issue": issue_id, "resolution": "shipped"}, "u-4")
    call(workspace, "/issues/reopen", {"issue": issue_id}, "u-5")

    assert trail_since(start) == [
        ("issue", issue_id, "create", "u-1"),
        ("issue", issue_id, "comment", "u-2"),
        ("issue", issue_id, "assign", "u-3"),
        ("issue", issue_id, "close", "u-4"),
        ("issue", issue_id, "reopen", "u-5"),
    ]


def test_every_agent_path_is_attributed_to_its_principal():
    workspace = Workspace("h-agents")
    start = mark()
    agent = call(workspace, "/agents", {"name": "triage", "owner": "u-1"}, "u-1")
    agent_id = agent["id"]
    call(workspace, "/agents/rename", {"agent": agent_id, "name": "triage-v2"}, "u-2")
    call(workspace, "/agents/rotate", {"agent": agent_id}, "u-3")
    call(workspace, "/agents/budget", {"agent": agent_id, "budget": 25}, "u-4")

    assert [row[2:] for row in trail_since(start)] == [
        ("register", "u-1"),
        ("rename", "u-2"),
        ("rotate_key", "u-3"),
        ("set_budget", "u-4"),
    ]


def test_squad_mutations_are_attributed_to_whoever_changed_the_membership():
    workspace = Workspace("h-squads")
    start = mark()
    squad = call(workspace, "/squads", {"name": "oncall"}, "u-1")
    squad_id = squad["id"]
    call(workspace, "/squads/members", {"squad": squad_id, "member": "u-2", "role": "dev"}, "u-2")
    call(workspace, "/squads/members/remove", {"squad": squad_id, "member": "u-2"}, "u-3")
    call(workspace, "/squads/archive", {"squad": squad_id}, "u-4")

    assert trail_since(start) == [
        ("squad", squad_id, "create", "u-1"),
        ("squad", squad_id, "add_member", "u-2"),
        ("squad", squad_id, "remove_member", "u-3"),
        ("squad", squad_id, "archive", "u-4"),
    ]


def test_run_lifecycle_over_http_is_attributed_request_by_request():
    workspace = Workspace("h-runs")
    agent = call(workspace, "/agents", {"name": "worker", "owner": "u-1"}, "u-1")
    start = mark()
    started = call(workspace, "/runs/start", {"agent": agent["id"], "steps": 2, "prompt": "go"}, "u-10")
    run_id = started["id"]
    call(workspace, "/runs/progress", {"run": run_id, "steps": 1, "tool_calls": 4}, "u-11")
    call(workspace, "/runs/finish", {"run": run_id, "summary": "done"}, "u-12")
    other = call(workspace, "/runs/start", {"agent": agent["id"], "steps": 1, "prompt": "again"}, "u-13")
    call(workspace, "/runs/cancel", {"run": other["id"], "reason": "stale"}, "u-14")

    assert actors_for(start, run_id, "start") == ["u-10"]
    assert actors_for(start, run_id, "progress") == ["u-10", "u-11"]
    assert actors_for(start, run_id, "finish") == ["u-12"]
    assert actors_for(start, other["id"]) == ["u-13", "u-13", "u-14"]
    assert DEFAULT_ACTOR not in [row[3] for row in trail_since(start)]


def test_scheduled_run_is_attributed_to_the_owner_of_the_schedule():
    workspace = Workspace("h-scheduled")
    agent = call(workspace, "/agents", {"name": "nightly", "owner": "u-1"}, "u-1")
    start = mark()
    run = execute_job(ScheduledJob(workspace, agent["id"], "summarise yesterday", "svc-nightly"))

    assert run["status"] == "finished"
    assert actors_for(start, run["id"]) == ["svc-nightly"] * 3
    assert actors_for(start, run["id"]) != [DEFAULT_ACTOR] * 3


def test_attribution_does_not_leak_between_consecutive_operators():
    workspace = Workspace("h-leak")
    job_a = ScheduledJob(workspace, "agent1", "sweep a", "ann")
    job_b = ScheduledJob(workspace, "agent1", "sweep b", "bob")
    start = mark()
    first = execute_job(job_a)
    issue = call(workspace, "/issues", {"title": "in between", "reporter": "cid"}, "cid")
    second = execute_job(job_b)

    assert first["id"] != second["id"]
    assert actors_for(start, first["id"]) == ["ann"] * 3
    assert actors_for(start, issue["id"]) == ["cid"]
    assert actors_for(start, second["id"]) == ["bob"] * 3
    assert DEFAULT_ACTOR not in [row[3] for row in trail_since(start)]


def test_a_full_workload_never_falls_back_to_the_local_default():
    workspace = Workspace("h-full")
    start = mark()
    agent = call(workspace, "/agents", {"name": "a1", "owner": "u-1"}, "u-1")
    call(workspace, "/agents/rename", {"agent": agent["id"], "name": "a2"}, "u-2")
    call(workspace, "/agents/rotate", {"agent": agent["id"]}, "u-3")
    call(workspace, "/agents/budget", {"agent": agent["id"], "budget": 5}, "u-4")
    issue = call(workspace, "/issues", {"title": "i1", "reporter": "u-5"}, "u-5")
    call(workspace, "/issues/comment", {"issue": issue["id"], "body": "note"}, "u-6")
    call(workspace, "/issues/assign", {"issue": issue["id"], "assignee": "u-7"}, "u-7")
    call(workspace, "/issues/close", {"issue": issue["id"], "resolution": "fixed"}, "u-8")
    call(workspace, "/issues/reopen", {"issue": issue["id"]}, "u-9")
    squad = call(workspace, "/squads", {"name": "s1"}, "u-10")
    call(workspace, "/squads/members", {"squad": squad["id"], "member": "u-11"}, "u-11")
    call(workspace, "/squads/members/remove", {"squad": squad["id"], "member": "u-11"}, "u-12")
    call(workspace, "/squads/archive", {"squad": squad["id"]}, "u-13")
    run = call(workspace, "/runs/start", {"agent": agent["id"], "steps": 1}, "u-14")
    call(workspace, "/runs/progress", {"run": run["id"], "steps": 2, "tool_calls": 3}, "u-15")
    call(workspace, "/runs/finish", {"run": run["id"], "summary": "ok"}, "u-16")
    execute_job(ScheduledJob(workspace, agent["id"], "nightly sweep", "svc-weekly"))

    rows = rows_since(start)
    assert len(rows) >= 19
    assert {field(row, "entity_type") for row in rows} == set(KINDS)
    assert [field(row, "actor") for row in rows if field(row, "actor") == DEFAULT_ACTOR] == []


def test_auth_guard_and_row_shape_survive_the_migration():
    workspace = Workspace("h-guard")
    issue = call(workspace, "/issues", {"title": "guarded", "reporter": "u-1"}, "u-1")
    start = mark()
    with pytest.raises(AuthenticationError):
        call(workspace, "/issues/assign", {"issue": issue["id"], "assignee": "u-2"}, user=None)
    assert rows_since(start) == []

    assigned = call(workspace, "/issues/assign", {"issue": issue["id"], "assignee": "u-2"}, "u-2")
    rows = rows_since(start)
    assert len(rows) == 1
    assert [field(row, "workspace") for row in rows] == ["h-guard"]
    assert field(rows[0], "entity_type") == "issue"
    assert field(rows[0], "actor") == "u-2"
    assert assigned["assignee"] == "u-2"
    assert "assignee" in field(rows[0], "details")
