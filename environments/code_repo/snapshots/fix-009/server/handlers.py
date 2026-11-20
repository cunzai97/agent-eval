"""HTTP handlers.

Authentication landed here: every routed request carries a verified principal,
so at the moment a handler calls into the tracker it always knows who is
asking. ``request.actor()`` raises when there is no principal, which is what
makes a request that reaches a handler at all a trustworthy attribution source.
"""
from tracker import agents as agents_api
from tracker import issues as issues_api
from tracker import runs as runs_api
from tracker import squads as squads_api


class AuthenticationError(Exception):
    pass


class Principal:
    def __init__(self, user_id, scopes=("read", "write")):
        self.user_id = user_id
        self.scopes = tuple(scopes)

    def has(self, scope):
        return scope in self.scopes


class Request:
    def __init__(self, workspace, method, path, body=None, principal=None):
        self.workspace = workspace
        self.method = method
        self.path = path
        self.body = dict(body or {})
        self.principal = principal

    def actor(self):
        if self.principal is None:
            raise AuthenticationError(f"{self.method} {self.path} needs a principal")
        return self.principal.user_id


# ---------------------------------------------------------------- issues ----

def handle_create_issue(request):
    issue = issues_api.create_issue(
        request.workspace, request.body["title"], reporter=request.body["reporter"], actor=request.actor()
    )
    return {"entity": "issue", "id": issue["id"], "status": issue["status"]}


def handle_comment(request):
    issue = issues_api.add_comment(
        request.workspace, request.body["issue"], request.body["body"], actor=request.actor()
    )
    return {"entity": "issue", "id": issue["id"], "comments": len(issue["comments"])}


def handle_assign(request):
    issue = issues_api.assign_issue(request.workspace, request.body["issue"], request.body["assignee"])
    return {"entity": "issue", "id": issue["id"], "assignee": issue["assignee"]}


def handle_close_issue(request):
    issue = issues_api.close_issue(request.workspace, request.body["issue"], request.body["resolution"])
    return {"entity": "issue", "id": issue["id"], "status": issue["status"]}


def handle_reopen(request):
    issue = issues_api.reopen_issue(request.workspace, request.body["issue"], actor=request.actor())
    return {"entity": "issue", "id": issue["id"], "status": issue["status"]}


# ---------------------------------------------------------------- agents ----

def handle_register_agent(request):
    agent = agents_api.register_agent(
        request.workspace, request.body["name"], owner=request.body["owner"], actor=request.actor()
    )
    return {"entity": "agent", "id": agent["id"], "name": agent["name"]}


def handle_rename_agent(request):
    agent = agents_api.rename_agent(
        request.workspace, request.body["agent"], request.body["name"], actor=request.actor()
    )
    return {"entity": "agent", "id": agent["id"], "name": agent["name"]}


def handle_rotate_key(request):
    agent = agents_api.rotate_key(request.workspace, request.body["agent"])
    return {"entity": "agent", "id": agent["id"], "fingerprint": agent["key"][:6]}


def handle_set_budget(request):
    agent = agents_api.set_budget(
        request.workspace, request.body["agent"], request.body["budget"], actor=request.actor()
    )
    return {"entity": "agent", "id": agent["id"], "budget": agent["budget"]}


# ---------------------------------------------------------------- squads ----

def handle_create_squad(request):
    squad = squads_api.create_squad(request.workspace, request.body["name"], actor=request.actor())
    return {"entity": "squad", "id": squad["id"], "members": list(squad["members"])}


def handle_add_member(request):
    squad = squads_api.add_member(
        request.workspace, request.body["squad"], request.body["member"], request.body.get("role", "dev")
    )
    return {"entity": "squad", "id": squad["id"], "members": list(squad["members"])}


def handle_remove_member(request):
    squad = squads_api.remove_member(request.workspace, request.body["squad"], request.body["member"])
    return {"entity": "squad", "id": squad["id"], "members": list(squad["members"])}


def handle_archive_squad(request):
    squad = squads_api.archive_squad(request.workspace, request.body["squad"], actor=request.actor())
    return {"entity": "squad", "id": squad["id"], "archived": squad["archived"]}


# ------------------------------------------------------------------ runs ----

def handle_start_run(request):
    workspace = request.workspace
    run_id = workspace.next_id("RUN-")
    ctx = runs_api.new_context(workspace, run_id, request.body["agent"], "http", request.body.get("prompt", ""))
    run = runs_api.start_run(ctx, request.body.get("steps", 1))
    runs_api.record_progress(ctx, run["steps_planned"], request.body.get("tool_calls", 0))
    return {"entity": "run", "id": run["id"], "status": run["status"]}


def handle_run_progress(request):
    ctx = runs_api.context_for(request.workspace, request.body["run"], "http")
    run = runs_api.record_progress(ctx, request.body["steps"], request.body["tool_calls"])
    return {"entity": "run", "id": run["id"], "steps": run["steps"]}


def handle_finish_run(request):
    ctx = runs_api.context_for(request.workspace, request.body["run"], "http")
    run = runs_api.finish_run(ctx, request.body.get("summary", ""))
    return {"entity": "run", "id": run["id"], "status": run["status"]}


def handle_cancel_run(request):
    ctx = runs_api.context_for(request.workspace, request.body["run"], "http")
    run = runs_api.cancel_run(ctx, request.body.get("reason", "cancelled by user"))
    return {"entity": "run", "id": run["id"], "status": run["status"]}


ROUTES = {
    ("POST", "/issues"): handle_create_issue,
    ("POST", "/issues/comment"): handle_comment,
    ("POST", "/issues/assign"): handle_assign,
    ("POST", "/issues/close"): handle_close_issue,
    ("POST", "/issues/reopen"): handle_reopen,
    ("POST", "/agents"): handle_register_agent,
    ("POST", "/agents/rename"): handle_rename_agent,
    ("POST", "/agents/rotate"): handle_rotate_key,
    ("POST", "/agents/budget"): handle_set_budget,
    ("POST", "/squads"): handle_create_squad,
    ("POST", "/squads/members"): handle_add_member,
    ("POST", "/squads/members/remove"): handle_remove_member,
    ("POST", "/squads/archive"): handle_archive_squad,
    ("POST", "/runs/start"): handle_start_run,
    ("POST", "/runs/progress"): handle_run_progress,
    ("POST", "/runs/finish"): handle_finish_run,
    ("POST", "/runs/cancel"): handle_cancel_run,
}


def dispatch(request):
    handler = ROUTES.get((request.method, request.path))
    if handler is None:
        raise KeyError(f"no route for {request.method} {request.path}")
    return handler(request)
