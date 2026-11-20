"""The scheduler worker.

Scheduled runs are not anonymous. A schedule belongs to the person who created
it, and ``ScheduledJob.requested_by`` carries that identity from the queue all
the way into this module. A run recorded here is a tracked mutation like any
other, so the trail has to say who the run was queued for.
"""
from tracker import runs as runs_api


class ScheduledJob:
    def __init__(self, workspace, agent, prompt, requested_by, steps=2, tool_calls=3, run_id=None):
        self.workspace = workspace
        self.agent = agent
        self.prompt = prompt
        self.requested_by = requested_by
        self.steps = steps
        self.tool_calls = tool_calls
        self.run_id = run_id

    def describe(self):
        return f"{self.run_id or 'queued'} by {self.requested_by} on {self.workspace.name}"


def execute_job(job):
    """Run one queued job start to finish and return the resulting run record."""
    job.run_id = job.run_id or job.workspace.next_id("RUN-")
    ctx = runs_api.new_context(job.workspace, job.run_id, job.agent, "scheduler", job.prompt)
    runs_api.start_run(ctx, job.steps)
    runs_api.record_progress(ctx, job.steps, job.tool_calls)
    runs_api.finish_run(ctx, "scheduled run finished")
    return dict(job.workspace.runs[job.run_id])
