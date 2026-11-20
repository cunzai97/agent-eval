# Audit trail contract

`audit.log.log_activity(workspace, entity_type, entity_id, action, details, actor=...)`
is the only write path into the trail.

* `entity_type` is one of `issue`, `agent`, `squad`, `run`.
* `actor` is **the authenticated principal that caused `action`** -- a user id
  such as `u-21`, or `svc-nightly` for a schedule.
* The `actor` keyword defaults to `local-user` only so that single-user local
  tooling keeps working. Every request routed here is authenticated, so an
  audit row carrying `local-user` after an HTTP call or a scheduled run is a
  bug: that call site dropped the actor instead of threading it. The default
  swallows the mistake, so nothing raises and the smoke tests stay green.

## Entry points that must supply an actor

| entry point | where the principal lives |
|---|---|
| `server.handlers.dispatch(request)` | `request.actor()` -> `request.principal.user_id` |
| `background.runner.execute_job(job)` | `job.requested_by` |

## Reviewing an attribution migration

Authentication used to be "always local". Migrating a call site means threading
the principal from the entry point down to `log_activity`, not only at the
places the smoke tests happen to poke:

1. `grep log_activity`, and check that every call site passes `actor=`;
2. for each site that does not, walk *up* to its entry point and add the missing
   parameter to every hop: handler -> tracker function,
   `ScheduledJob` -> `runs.new_context` -> `RunContext` -> the run helpers,
   `context_for` included;
3. the run paths carry identity on a context object, so `RunContext` and its
   two constructors are part of the plumbing, not only the record calls;
4. attribution must not leak: two consecutive requests from different
   principals, and two scheduled jobs owned by different people, must each be
   recorded under their own actor.

Keep the public interfaces (`audit.log`, `server.handlers.Request/Principal/
dispatch`, `background.runner.ScheduledJob/execute_job`, the `tracker.*`
functions) working as they are; `tests/` drives them.

`tests/` is a smoke suite for the issue and agent paths. It checks the trail
*shape*, and checks `actor` only for the call sites that were migrated first.
