import gc

from checkpoint.index import CheckpointIndex
from checkpoint.model import Checkpoint, Session
from checkpoint.selector import choose_restore


def keep(index, *checkpoints):
    for checkpoint in checkpoints:
        index.add(checkpoint)
    return checkpoints


def test_returns_longest_exact_prefix_not_first_bucket():
    index = CheckpointIndex()
    short, long = keep(
        index,
        Checkpoint(2, (10, 20)),
        Checkpoint(4, (10, 20, 30, 40)),
    )
    assert index.find((10, 20, 30, 40, 99)) is long
    assert short.n_tokens == 2


def test_fingerprint_collision_is_not_identity():
    index = CheckpointIndex()
    wrong, = keep(index, Checkpoint(2, (1, 4)))
    assert wrong.fingerprint == Checkpoint(2, (2, 3)).fingerprint
    assert index.find((2, 3, 9)) is None


def test_rejects_inconsistent_identity_length():
    index = CheckpointIndex()
    bad, good = keep(
        index,
        Checkpoint(3, (1, 2)),
        Checkpoint(2, (1, 2)),
    )
    assert index.find((1, 2, 3)) is good
    assert bad.n_tokens == 3


def test_checkpoint_longer_than_request_is_ignored():
    index = CheckpointIndex()
    long, = keep(index, Checkpoint(4, (1, 2, 3, 4)))
    assert index.find((1, 2, 3)) is None
    assert long.n_tokens == 4


def test_root_is_opt_in():
    index = CheckpointIndex()
    root, = keep(index, Checkpoint(2, (7, 8), full=True, is_root=True))
    assert index.find((7, 8, 9)) is None
    assert index.find((7, 8, 9), include_root=True) is root


def test_full_only_does_not_return_partial_checkpoint():
    index = CheckpointIndex()
    partial, full = keep(
        index,
        Checkpoint(4, (1, 2, 3, 4), full=False),
        Checkpoint(2, (1, 2), full=True),
    )
    assert index.find((1, 2, 3, 4, 5), full_only=True) is full
    assert partial.full is False


def test_dead_weak_references_are_pruned():
    index = CheckpointIndex()
    checkpoint = Checkpoint(2, (1, 2))
    index.add(checkpoint)
    del checkpoint
    gc.collect()
    assert index.find((1, 2, 3)) is None
    assert index.live_entry_count() == 0


def test_full_anchor_is_selected_before_costly_leaf_load():
    index = CheckpointIndex()
    anchor, = keep(index, Checkpoint(4, (1, 2, 3, 4), full=True, owner_id="branch-b"))
    leaves = [
        Session("leaf-d", (1, 2, 3, 4, 8, 8, 8), 500),
        Session("leaf-c", (1, 2, 3, 9, 9), 300),
    ]
    kind, selected, reusable = choose_restore((1, 2, 3, 4, 7), index, leaves)
    assert (kind, selected, reusable) == ("anchor", anchor, 4)


def test_leaf_lcp_is_fallback_when_no_full_anchor_exists():
    index = CheckpointIndex()
    partial, = keep(index, Checkpoint(4, (1, 2, 3, 4), full=False))
    leaf_a = Session("a", (1, 2, 8), 10)
    leaf_b = Session("b", (1, 2, 3, 9), 20)
    kind, selected, reusable = choose_restore((1, 2, 3, 4, 5), index, [leaf_a, leaf_b])
    assert (kind, selected, reusable) == ("leaf", leaf_b, 3)
    assert partial.full is False
