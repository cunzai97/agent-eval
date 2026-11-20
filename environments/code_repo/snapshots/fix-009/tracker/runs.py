"""Run bookkeeping for agent work.

A run is never mutated straight from a request: the caller first builds a
``RunContext`` and hands that around, so everything a run path knows about the
outside world has to be inside the context.
"""
from audit.log import log_activity

ENTITY = "run"


class RunContext:
    def __init__(self, workspace, run_id, agent, source, prompt):
        self.workspace = workspace
        self.run_id = run_id
        self.agent = agent
        self.source = source  # "http" | "scheduler" | "cli"
        self.prompt = prompt

    def snapshot(self):
        return {
            "run_id": self.run_id,
            "agent": self.agent,
            "source": self.source,
            "workspace": self.workspace.name,
        }


def new_context(workspace, run_id, agent, source, prompt):
    """Context for a run that has not been registered yet."""
    return RunContext(workspace, run_id, agent, source, prompt)


def context_for(workspace, run_id, source):
    """Context rebuilt from an existing run, for the stateless HTTP handlers."""
    run = workspace.runs.get(run_id)
    if run is None:
        raise KeyError(f"no run {run_id} in {workspace.name}")
    return RunContext(workspace, run_id, run["agent"], source, run["prompt"])


def _require(ctx):
    run = ctx.workspace.runs.get(ctx.run_id)
    if run is None:
        raise KeyError(f"no run {ctx.run_id} in {ctx.workspace.name}")
    return run


def start_run(ctx, steps):
    run = {
        "id": ctx.run_id,
        "agent": ctx.agent,
        "source": ctx.source,
        "prompt": ctx.prompt,
        "status": "running",
        "steps": 0,
        "tool_calls": 0,
        "steps_planned": int(steps),
    }
    ctx.workspace.runs[ctx.run_id] = run
    log_activity(
        ctx.workspace.name, ENTITY, ctx.run_id, "start",
        {"agent": ctx.agent, "source": ctx.source, "steps": int(steps)},
    )
    return dict(run)


def record_progress(ctx, steps, tool_calls):
    run = _require(ctx)
    run["steps"] += int(steps)
    run["tool_calls"] += int(tool_calls)
    log_activity(
        ctx.workspace.name, ENTITY, ctx.run_id, "progress",
        {"steps": int(steps), "tool_calls": int(tool_calls)},
    )
    return dict(run)


def finish_run(ctx, summary):
    run = _require(ctx)
    run["status"] = "finished"
    log_activity(ctx.workspace.name, ENTITY, ctx.run_id, "finish", {"chars": len(summary)})
    return dict(run)


def cancel_run(ctx, reason):
    run = _require(ctx)
    run["status"] = "cancelled"
    log_activity(ctx.workspace.name, ENTITY, ctx.run_id, "cancel", {"reason": reason})
    return dict(run)
