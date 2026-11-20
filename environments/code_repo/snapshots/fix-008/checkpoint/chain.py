from .model import Checkpoint


class CheckpointChain:
    def __init__(self, capacity=3, min_step=16_384):
        self.capacity = capacity
        self.min_step = min_step
        self.root = None
        self.anchors = []

    def set_root(self, tokens):
        # Root was incorrectly mixed into the ordinary A/B/C budget.
        root = Checkpoint(len(tokens), tuple(tokens), is_root=True)
        self.root = root
        self.anchors.append(root)
        return root

    def offer(self, position, tokens, *, boundary):
        # Every prefill split currently becomes a checkpoint.  The ring also
        # drops A unconditionally, which destroys dense-tail replay coverage.
        checkpoint = Checkpoint(position, tuple(tokens))
        self.anchors.append(checkpoint)
        if len(self.anchors) > self.capacity:
            self.anchors.pop(0)
        return checkpoint

    def find_restore(self, request_tokens):
        # Fingerprints are only bucket hints, but this code treats them as identity.
        for checkpoint in self.anchors:
            if checkpoint.position <= len(request_tokens):
                if checkpoint.fingerprint == sum(request_tokens[:checkpoint.position]) % 13:
                    return checkpoint
        return self.root
