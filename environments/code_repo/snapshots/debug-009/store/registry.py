"""Entity kind registry.

A *kind* is a top level entity the store keeps. Adding one is a mirroring job:
every literal list below (and the ones living in db/portable/state/render/cli)
is consumed by a different subsystem, and no subsystem cross-checks another.
Nothing here validates that the collections agree, so a half registered kind
behaves correctly for the paths that were mirrored and silently drops data for
the ones that were not.
"""

# Kinds accepted by JsonStore._doc(); anything else raises ValueError.
KINDS = ["issue", "agent", "squad"]

# Column headers used by the board payload and by `boardstore list`.
LABELS = {
    "issue": "Issues",
    "agent": "Agents",
    "squad": "Squads",
}

# Order in which the store walks *every* collection (repair, stats, sequences).
COLLECTION_ORDER = ["issue", "agent", "squad"]


def is_kind(value):
    return value in KINDS


def label(kind):
    return LABELS[kind]
