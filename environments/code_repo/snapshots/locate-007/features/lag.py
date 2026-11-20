def lagged(values):
    """Causal one-period lag; the first period has no feature."""
    return [None, *list(values)[:-1]]
