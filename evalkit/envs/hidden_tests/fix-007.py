import gc

from checkpoint.index import CheckpointIndex
from checkpoint.model import Checkpoint, Session
from checkpoint.selector import choose_restore


def test_prunes_dead_entries_from_all_buckets_during_lookup():
    index = CheckpointIndex()
    dead_short = Checkpoint(1, (9,))
    dead_long = Checkpoint(20, tuple(range(20)))
    index.add(dead_short)
    index.add(dead_long)
    del dead_short, dead_long
    gc.collect()
    assert index.find((1, 2, 3)) is None
    assert index.live_entry_count() == 0


def test_full_only_still_excludes_root_unless_opted_in():
    index = CheckpointIndex()
    root = Checkpoint(3, (1, 2, 3), full=True, is_root=True)
    index.add(root)
    assert index.find((1, 2, 3, 4), full_only=True) is None
    assert index.find((1, 2, 3, 4), full_only=True, include_root=True) is root


def test_invalid_longer_identity_never_matches_short_declared_length():
    index = CheckpointIndex()
    invalid = Checkpoint(2, (4, 5, 6), full=True)
    valid = Checkpoint(1, (4,), full=True)
    index.add(invalid)
    index.add(valid)
    assert index.find((4, 5, 6, 7), full_only=True) is valid


def test_zero_lcp_leaf_is_not_a_restore_source():
    index = CheckpointIndex()
    leaf = Session("unrelated", (8, 8, 8), 1)
    assert choose_restore((1, 2, 3), index, [leaf]) == (None, None, 0)


def test_root_is_not_implicitly_used_as_full_anchor():
    index = CheckpointIndex()
    root = Checkpoint(3, (1, 2, 3), full=True, is_root=True)
    index.add(root)
    leaf = Session("branch", (1, 2, 9), 5)
    kind, selected, reusable = choose_restore((1, 2, 3, 4), index, [leaf])
    assert (kind, selected, reusable) == ("leaf", leaf, 2)
