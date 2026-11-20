from cache.key import profile_key


_CACHE = {}


def clear_cache():
    _CACHE.clear()


def get_profile(tenant_id: str, user_id: str, repository):
    key = profile_key(tenant_id, user_id)
    if key not in _CACHE:
        _CACHE[key] = repository[(tenant_id, user_id)].copy()
    return _CACHE[key].copy()
