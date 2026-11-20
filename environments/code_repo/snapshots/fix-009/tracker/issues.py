"""Issue mutations."""
from audit.log import log_activity

ENTITY = "issue"


def _require(ws, issue_id):
    issue = ws.issues.get(issue_id)
    if issue is None:
        raise KeyError(f"no issue {issue_id} in {ws.name}")
    return issue


def create_issue(ws, title, *, reporter, actor):
    issue_id = ws.next_id("ISSUE-")
    issue = {
        "id": issue_id,
        "title": title,
        "reporter": reporter,
        "status": "open",
        "assignee": None,
        "labels": [],
        "comments": [],
    }
    ws.issues[issue_id] = issue
    log_activity(ws.name, ENTITY, issue_id, "create", {"title": title, "reporter": reporter}, actor=actor)
    return dict(issue)


def add_comment(ws, issue_id, body, *, actor):
    issue = _require(ws, issue_id)
    issue["comments"].append({"body": body, "author": actor})
    log_activity(ws.name, ENTITY, issue_id, "comment", {"chars": len(body)}, actor=actor)
    return dict(issue)


def assign_issue(ws, issue_id, assignee):
    issue = _require(ws, issue_id)
    issue["assignee"] = assignee
    log_activity(ws.name, ENTITY, issue_id, "assign", {"assignee": assignee})
    return dict(issue)


def close_issue(ws, issue_id, resolution):
    issue = _require(ws, issue_id)
    issue["status"] = "closed"
    issue["resolution"] = resolution
    log_activity(ws.name, ENTITY, issue_id, "close", {"resolution": resolution})
    return dict(issue)


def reopen_issue(ws, issue_id, *, actor):
    issue = _require(ws, issue_id)
    issue["status"] = "open"
    issue.pop("resolution", None)
    log_activity(ws.name, ENTITY, issue_id, "reopen", {}, actor=actor)
    return dict(issue)
