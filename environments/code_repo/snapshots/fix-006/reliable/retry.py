from .backoff import delay_for


def retry_call(fn, attempts=3, base_delay=0.1, max_delay=10.0, sleep=lambda _: None):
    """Call fn with at most ``attempts`` total attempts."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception:
            sleep(delay_for(attempt, base_delay, max_delay))
