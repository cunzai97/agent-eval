from cache.store import TTLCache
from flags.service import FlagService


class Clock:
    def __init__(self, milliseconds=1_000_000):
        self.now = milliseconds

    def __call__(self):
        return self.now

    def advance_seconds(self, seconds):
        self.now += seconds * 1000


def test_value_lives_until_ttl_boundary():
    clock = Clock()
    cache = TTLCache(clock)
    cache.put("k", "v", 30)
    clock.advance_seconds(29.999)
    assert cache.get("k") == "v"
    clock.advance_seconds(0.001)
    assert cache.get("k") is None


def test_expired_entry_is_removed():
    clock = Clock()
    cache = TTLCache(clock)
    cache.put("k", "v", 1)
    clock.advance_seconds(1)
    assert cache.get("k") is None
    assert len(cache) == 0


def test_zero_ttl_is_immediately_expired():
    cache = TTLCache(Clock())
    cache.put("k", "v", 0)
    assert cache.get("k", "missing") == "missing"


def test_negative_ttl_is_rejected():
    cache = TTLCache(Clock())
    try:
        cache.put("k", "v", -1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative TTL accepted")


def test_falsey_values_are_not_replaced_by_default():
    cache = TTLCache(Clock())
    for key, value in [("false", False), ("zero", 0), ("empty", "")]:
        cache.put(key, value, 10)
        assert cache.get(key, "fallback") == value


def test_none_from_loader_is_cached():
    cache = TTLCache(Clock())
    calls = []

    def load():
        calls.append(1)
        return None

    assert cache.get_or_load("k", load, 10) is None
    assert cache.get_or_load("k", load, 10) is None
    assert len(calls) == 1


def test_false_flag_does_not_hit_backend_twice():
    clock = Clock()
    calls = []

    class Backend:
        def fetch(self, user_id, flag):
            calls.append((user_id, flag))
            return False

    service = FlagService(TTLCache(clock), Backend(), ttl_seconds=30)
    assert service.enabled("u1", "new-ui") is False
    assert service.enabled("u1", "new-ui") is False
    assert calls == [("u1", "new-ui")]


def test_flag_reloads_only_after_expiry():
    clock = Clock()
    values = iter([True, False])

    class Backend:
        def fetch(self, user_id, flag):
            return next(values)

    service = FlagService(TTLCache(clock), Backend(), ttl_seconds=30)
    assert service.enabled("u1", "new-ui") is True
    clock.advance_seconds(29)
    assert service.enabled("u1", "new-ui") is True
    clock.advance_seconds(1)
    assert service.enabled("u1", "new-ui") is False
