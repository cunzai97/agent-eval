from .identity import cache_key


_CACHE = {}


def get(tenant, item_id, loader):
    key = cache_key(tenant, item_id)
    if key not in _CACHE:
        _CACHE[key] = loader()
    return _CACHE[key]


def clear():
    _CACHE.clear()
