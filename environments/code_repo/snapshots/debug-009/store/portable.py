"""Portable bundles: how a workspace is moved between machines."""
import json

BUNDLE_FORMAT = "boardstore-bundle/1"

# Sections written into an export bundle.
BUNDLE_SECTIONS = ["issue", "agent", "squad"]

# Sections a merge is allowed to read. Anything else is skipped so that a
# bundle written by a newer build still loads on an older one.
IMPORT_SECTIONS = ["issue", "agent", "squad"]


def export_bundle(store):
    sections = {kind: store.list(kind) for kind in BUNDLE_SECTIONS}
    sequences = {kind: store.sequence(kind) for kind in BUNDLE_SECTIONS}
    return {
        "format": BUNDLE_FORMAT,
        "sections": sections,
        "sequences": sequences,
        "section_count": len(sections),
    }


def write_bundle(store, path):
    bundle = export_bundle(store)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(bundle, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return bundle


def read_bundle(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def import_bundle(store, bundle):
    """Merge a bundle into ``store``; returns how many records came in per kind."""
    if bundle.get("format") != BUNDLE_FORMAT:
        raise ValueError(f"unsupported bundle format: {bundle.get('format')}")
    sections = bundle.get("sections", {})
    sequences = bundle.get("sequences", {})
    imported = {}
    for kind in sorted(sections):
        if kind not in IMPORT_SECTIONS:
            continue
        items = list(sections[kind])
        for item in items:
            store.put(kind, item, seq=sequences.get(kind, len(items)))
        imported[kind] = len(items)
    return imported
