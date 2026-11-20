class FlagService:
    def __init__(self, cache, backend, ttl_seconds=30):
        self.cache = cache
        self.backend = backend
        self.ttl_seconds = ttl_seconds

    def enabled(self, user_id, flag):
        key = (user_id, flag)
        return self.cache.get_or_load(
            key,
            lambda: self.backend.fetch(user_id, flag),
            self.ttl_seconds,
        )
