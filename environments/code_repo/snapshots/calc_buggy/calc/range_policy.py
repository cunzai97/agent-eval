from .bounds import inclusive_stop


def inclusive_values(start, end):
    """Return integer values from start through end, inclusive."""
    if end < start - 1:
        return []
    return range(start, inclusive_stop(end))
