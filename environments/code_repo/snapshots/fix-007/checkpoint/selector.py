from .model import common_prefix


def choose_restore(tokens, index, sessions):
    """Return (kind, object, reusable_tokens), or (None, None, 0)."""
    best = None
    best_lcp = 0
    for session in sessions:
        lcp = common_prefix(session.tokens, tokens)
        if lcp > best_lcp:
            best = session
            best_lcp = lcp

    if best is not None:
        return "leaf", best, best_lcp

    anchor = index.find(tokens, full_only=True)
    if anchor is not None:
        return "anchor", anchor, anchor.n_tokens
    return None, None, 0
