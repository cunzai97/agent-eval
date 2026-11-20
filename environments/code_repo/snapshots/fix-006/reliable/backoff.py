def delay_for(retry_number, base_delay, max_delay):
    """Return delay before retry_number (retry_number starts at 1)."""
    return min(max_delay, base_delay * (2 ** retry_number))
