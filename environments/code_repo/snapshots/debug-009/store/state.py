"""State payloads handed to the daemon heartbeat and to the web board."""
from .registry import LABELS

# Kinds counted in the heartbeat payload.
COUNT_KINDS = ["issue", "agent", "squad"]

# Kinds rendered as board columns, in display order.
GROUP_KINDS = ["issue", "agent", "squad"]


def daemon_state(store):
    return {
        "root": store.root,
        "counts": {kind: store.count(kind) for kind in COUNT_KINDS},
        "sequences": {kind: store.sequence(kind) for kind in COUNT_KINDS},
        "collections": len(COUNT_KINDS),
    }


def board_state(store):
    groups = []
    for kind in GROUP_KINDS:
        items = store.list(kind)
        groups.append({
            "kind": kind,
            "label": LABELS[kind],
            "size": len(items),
            "items": items,
        })
    return {"groups": groups, "total": sum(group["size"] for group in groups)}
