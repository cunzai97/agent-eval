class PromptCache:
    def __init__(self, chain, worker):
        self.chain = chain
        self.worker = worker

    def save_boundary(self, seq, position, tokens, *, boundary):
        checkpoint = self.chain.offer(position, tokens, boundary=boundary)
        self.worker.drain(seq)
        return checkpoint


def should_save_session(current_conversation, incoming_conversation, f_keep):
    # This permissive threshold creates a new serialized leaf on small tool-call rewinds.
    return current_conversation != incoming_conversation or f_keep < 0.95
