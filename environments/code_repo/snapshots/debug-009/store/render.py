"""One-line card renderer used by list output and by notifications."""

# kind -> renderer. A kind with no entry falls back to the placeholder below.
RENDERERS = {
    "issue": lambda item: f"{item['id']} {item.get('title', '')} [{item.get('status', 'open')}]",
    "agent": lambda item: f"{item['id']} {item.get('name', '')} mode={item.get('mode', 'chat')}",
    "squad": lambda item: f"{item['id']} {item.get('name', '')} members={len(item.get('members', []))}",
}

PLACEHOLDER = "?{id} unsupported kind {kind}"


def render_card(item):
    kind = item.get("kind")
    renderer = RENDERERS.get(kind)
    if renderer is None:
        return PLACEHOLDER.format(id=item.get("id", ""), kind=kind)
    return renderer(item)


def render_all(store, kind):
    return [render_card(item) for item in store.list(kind)]
