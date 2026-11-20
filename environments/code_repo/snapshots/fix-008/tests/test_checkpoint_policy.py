from checkpoint.chain import CheckpointChain
from server.prompt_cache import PromptCache, should_save_session


class Worker:
    def __init__(self):
        self.events = []

    def drain(self, seq):
        self.events.append(("drain", seq))


def add(chain, pos, token):
    return chain.offer(pos, tuple([token] * pos), boundary="message")


def test_root_does_not_consume_ordinary_capacity():
    chain = CheckpointChain(capacity=3, min_step=10)
    chain.set_root((1, 2))
    add(chain, 20, 2)
    add(chain, 40, 3)
    add(chain, 60, 4)
    assert [item.position for item in chain.anchors] == [20, 40, 60]
    assert chain.root.position == 2


def test_non_message_prefill_splits_do_not_create_checkpoints():
    chain = CheckpointChain()
    assert chain.offer(2, (1, 2), boundary="ubatch") is None
    assert chain.offer(2, (1, 2), boundary="tail") is None
    assert chain.anchors == []


def test_dense_b_c_causes_d_to_replace_c():
    chain = CheckpointChain(capacity=3, min_step=16)
    a = add(chain, 10, 1)
    b = add(chain, 30, 2)
    add(chain, 40, 3)
    d = add(chain, 70, 4)
    assert chain.anchors == [a, b, d]


def test_well_spaced_b_c_causes_oldest_eviction():
    chain = CheckpointChain(capacity=3, min_step=16)
    add(chain, 10, 1)
    b = add(chain, 30, 2)
    c = add(chain, 50, 3)
    d = add(chain, 70, 4)
    assert chain.anchors == [b, c, d]


def test_duplicate_identity_is_a_noop():
    chain = CheckpointChain()
    first = add(chain, 3, 7)
    second = add(chain, 3, 7)
    assert second is first
    assert chain.anchors == [first]


def test_restore_is_longest_exact_identity_not_fingerprint():
    chain = CheckpointChain(capacity=4)
    short = chain.offer(2, (1, 2), boundary="message")
    chain.offer(3, (8, 11, 0), boundary="message")
    long = chain.offer(4, (1, 2, 3, 4), boundary="message")
    assert (8 + 11 + 0) % 13 == (1 + 2 + 3) % 13
    assert chain.find_restore((1, 2, 3, 4, 5)) is long
    assert short.position == 2


def test_root_is_exact_fallback_only():
    chain = CheckpointChain()
    root = chain.set_root((5, 6))
    add(chain, 4, 8)
    assert chain.find_restore((5, 6, 7)) is root
    assert chain.find_restore((5, 7, 7)) is None


def test_worker_is_drained_before_snapshot_is_published():
    events = []

    class RecordingChain(CheckpointChain):
        def offer(self, position, tokens, *, boundary):
            events.append(("offer", position))
            return super().offer(position, tokens, boundary=boundary)

    class RecordingWorker(Worker):
        def drain(self, seq):
            events.append(("drain", seq))

    cache = PromptCache(RecordingChain(), RecordingWorker())
    cache.save_boundary(3, 2, (1, 2), boundary="message")
    assert events == [("drain", 3), ("offer", 2)]


def test_small_same_conversation_rewind_does_not_serialize_leaf():
    assert should_save_session("chat-a", "chat-a", 0.96) is False
    assert should_save_session("chat-a", "chat-a", 0.49) is True
    assert should_save_session("chat-a", "chat-b", 0.99) is True
