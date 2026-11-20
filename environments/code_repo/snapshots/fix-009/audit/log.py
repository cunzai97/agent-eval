"""The audit trail: one row per tracked mutation, and one write path.

``log_activity(workspace, entity_type, entity_id, action, details, actor=...)`` is
the only way a mutation reaches the trail. ``actor`` answers *who caused this*,
and it carries a default so that a call site which forgot to pass one still
works -- it just attributes the event to whoever happens to be installed
locally. Nothing raises, no test fails, and the trail quietly lies.

Keeping the signature compatible with the pre-auth code is deliberate: call
sites are added over time, so the way to tell a trustworthy trail from a broken
one is to read every call site, not to wait for an exception.
"""

DEFAULT_ACTOR = "local-user"


class AuditEvent:
    def __init__(self, seq, workspace, entity_type, entity_id, action, details, actor):
        self.seq = seq
        self.workspace = workspace
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.action = action
        self.details = details
        self.actor = actor

    def as_dict(self):
        return {
            "seq": self.seq,
            "workspace": self.workspace,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "details": dict(self.details),
            "actor": self.actor,
        }

    def __repr__(self):
        return (
            f"<AuditEvent {self.seq} {self.workspace}/{self.entity_type}:{self.entity_id} "
            f"{self.action} by {self.actor}>"
        )


class AuditTrail:
    def __init__(self):
        self.events = []

    def record(self, workspace, entity_type, entity_id, action, details=None, *, actor=DEFAULT_ACTOR):
        event = AuditEvent(
            len(self.events) + 1, workspace, entity_type, entity_id, action, dict(details or {}), actor
        )
        self.events.append(event)
        return event

    def reset(self):
        self.events = []


_TRAIL = AuditTrail()


def trail():
    return _TRAIL


def log_activity(workspace, entity_type, entity_id, action, details=None, *, actor=DEFAULT_ACTOR):
    """Append one audit row. ``actor`` must be the principal that caused ``action``."""
    return _TRAIL.record(workspace, entity_type, entity_id, action, details, actor=actor)


def events():
    return list(_TRAIL.events)


def events_for(entity_type, entity_id=None, workspace=None):
    found = []
    for event in _TRAIL.events:
        if event.entity_type != entity_type:
            continue
        if entity_id is not None and event.entity_id != entity_id:
            continue
        if workspace is not None and event.workspace != workspace:
            continue
        found.append(event)
    return found


def reset():
    _TRAIL.reset()
