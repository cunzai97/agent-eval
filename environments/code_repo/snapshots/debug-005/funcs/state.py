COUNTS = {}


def consume(name, limit):
    COUNTS[name] = COUNTS.get(name, 0) + 1
    if COUNTS[name] > limit:
        raise RuntimeError("call limit exceeded")
