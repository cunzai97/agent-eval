_MISSING = object()


class TTLCache:
    def __init__(self, clock):
        self._clock = clock
        self._items = {}

    def put(self, key, value, ttl_seconds):
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        # The clock reports milliseconds; expiry is accidentally mixed with seconds.
        expires_at = self._clock() + ttl_seconds
        self._items[key] = (value, expires_at)

    def get(self, key, default=None):
        item = self._items.get(key, _MISSING)
        if item is _MISSING:
            return default
        value, expires_at = item
        if self._clock() >= expires_at:
            del self._items[key]
            return default
        return value or default

    def get_or_load(self, key, loader, ttl_seconds):
        value = self.get(key)
        if value is not None:
            return value
        value = loader()
        self.put(key, value, ttl_seconds)
        return value

    def __len__(self):
        return len(self._items)
