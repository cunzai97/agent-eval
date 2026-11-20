import weakref


class CheckpointIndex:
    def __init__(self):
        self._by_length = {}

    def add(self, checkpoint):
        self._by_length.setdefault(checkpoint.n_tokens, []).append(weakref.ref(checkpoint))

    def find(self, tokens, *, include_root=False, full_only=False):
        # A compact fingerprint was assumed to be identity, and ascending lengths
        # make a short checkpoint win before a better branch checkpoint is checked.
        for length in sorted(self._by_length):
            if length > len(tokens):
                continue
            expected = sum(tokens[:length]) % 17
            for ref in self._by_length[length]:
                checkpoint = ref()
                if checkpoint is not None and checkpoint.fingerprint == expected:
                    return checkpoint
        return None

    def live_entry_count(self):
        return sum(len(refs) for refs in self._by_length.values())
