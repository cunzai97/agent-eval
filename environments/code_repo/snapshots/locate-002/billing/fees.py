from .cache_key import fee_cache_key


_CACHE = {}


def service_fee(order_id, subtotal, customer_tier):
    key = fee_cache_key(order_id, subtotal, customer_tier)
    if key not in _CACHE:
        _CACHE[key] = 0 if customer_tier == "vip" else 20
    return _CACHE[key]


def clear():
    _CACHE.clear()
