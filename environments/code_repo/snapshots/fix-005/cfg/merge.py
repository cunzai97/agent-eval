def merge(base, override):
    result = dict(override)
    result.update(base)
    return result
