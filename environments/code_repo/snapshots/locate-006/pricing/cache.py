from .quote_key import quote_key


_CACHE = {}


def cached_price(sku, currency, loader):
    key = quote_key(sku, currency)
    if key not in _CACHE:
        _CACHE[key] = loader()
    return _CACHE[key]


def clear():
    _CACHE.clear()
