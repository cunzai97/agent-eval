import pytest

from checkpoint.chain import CheckpointChain
from server.prompt_cache import PromptCache, should_save_session


def test_identity_length_must_equal_position():
    chain = CheckpointChain()
    with pytest.raises(ValueError):
        chain.offer(3, (1, 2), boundary="message")
    assert chain.anchors == []


def test_ignored_boundary_does_not_force_async_drain():
    class Worker:
        def __init__(self):
            self.calls = []

        def drain(self, seq):
            self.calls.append(seq)

    worker = Worker()
    cache = PromptCache(CheckpointChain(), worker)
    assert cache.save_boundary(0, 2, (1, 2), boundary="ubatch") is None
    assert worker.calls == []


def test_exact_half_keep_does_not_create_same_conversation_leaf():
    assert should_save_session("x", "x", 0.5) is False
    assert should_save_session("x", "x", 0.0) is True


def test_rejected_collision_falls_back_to_exact_root():
    chain = CheckpointChain(capacity=3)
    root = chain.set_root((1, 2))
    chain.offer(3, (8, 11, 0), boundary="message")
    assert chain.find_restore((1, 2, 3, 4)) is root


def test_replacing_dense_c_preserves_capacity_and_order_repeatedly():
    chain = CheckpointChain(capacity=3, min_step=100)
    chain.offer(10, (1,) * 10, boundary="message")
    chain.offer(200, (2,) * 200, boundary="message")
    chain.offer(220, (3,) * 220, boundary="message")
    chain.offer(240, (4,) * 240, boundary="message")
    chain.offer(500, (5,) * 500, boundary="message")
    assert [item.position for item in chain.anchors] == [10, 200, 500]
