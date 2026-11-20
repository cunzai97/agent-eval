"""Hidden checks for snapshot debug-006 (debug-stacktrace-006).

The prompt reports two symptoms -- a 30 s TTL reloading after 1 s, and legitimate
`False` / `0` / `""` treated as "not cached" -- and the visible suite pins each
with one example.  That is enough for a fix that hard-codes the shape:
`expires_at = 1_000_000 + ttl * 1000` (an absolute base instead of "now"),
`if ttl_seconds > 100: ttl *= 1000`, flushing the whole dict when one entry
expires, `return value if value is not None else default` (which cures the
falsey report but still cannot cache a `None`), or a `FlagService` that quietly
stops using the cache.

These checks drive the contract the prompt states -- TTL units and boundary,
expired entries really deleted, and *any* value a loader returns (including
`None`) cacheable -- along dimensions the visible suite never touches:

* TTLs of 0.5 / 5 / 30 / 90 s each live until their own boundary and die at it,
  so the unit conversion has to be general and the boundary stays `>=`;
* expiry is measured from the moment of the `put`, and re-reading an entry does
  not extend its life;
* entries expire on their own: a still-fresh key survives both a `get` and a
  `get_or_load` aimed at an expired key, and the expired record is evicted
  rather than the whole cache being flushed;
* every falsey payload (`False`, `0`, `0.0`, `""`, `[]`, `{}`, `None`) survives
  `get(key, default)` and counts as a hit for `get_or_load`, whose loader must
  not be consulted again -- `None` included, the one case a `value is not None`
  test cannot express;
* a genuine miss still loads, an *expired* entry is not a hit, re-`put`ing a key
  replaces value and deadline in both directions, a negative TTL is rejected and
  a TTL of `0` expires at once;
* `FlagService` inherits all of it through the cache: independent
  `(user, flag)` keys, a `ttl_seconds` other than the 30 the visible tests happen
  to use honoured exactly, and payloads chosen so a stale value could not
  coincidentally look correct.

Left unasserted on purpose: `len()` before any access touches an expired key
(lazy eviction is a legitimate reading of "expired entries are deleted"), the
public signatures, and thread safety.
"""
import pathlib
import shutil

for _stale in pathlib.Path(__file__).resolve().parent.rglob("__pycache__"):
    shutil.rmtree(_stale, ignore_errors=True)

import pytest

from cache.store import TTLCache
from flags.service import FlagService


class Clock:
    """The clock the snapshot hands the cache: it reports milliseconds."""

    def __init__(self, milliseconds=1_000_000):
        self.now = milliseconds

    def __call__(self):
        return self.now

    def advance_seconds(self, seconds):
        self.now += seconds * 1000


def boom():
    raise AssertionError("loader must not be consulted for a cached key")


def test_ttl_is_seconds_for_every_scale_and_dies_on_its_own_boundary():
    for ttl, alive_at, gone_at in [(0.5, 0.4, 0.5), (5, 4.999, 5), (30, 29, 30), (90, 89.5, 90)]:
        clock = Clock()
        cache = TTLCache(clock)
        cache.put("k", "v", ttl)
        clock.advance_seconds(alive_at)
        assert cache.get("k") == "v", f"ttl={ttl} lost its value at {alive_at}s"
        clock.advance_seconds(gone_at - alive_at)
        assert cache.get("k") is None, f"ttl={ttl} still cached at {gone_at}s"
        assert cache.get("k", "gone") == "gone"


def test_expiry_is_measured_from_the_put_and_reading_does_not_extend_it():
    clock = Clock()
    cache = TTLCache(clock)
    clock.advance_seconds(20)
    cache.put("k", "late", 30)
    clock.advance_seconds(29)
    assert cache.get("k") == "late"
    clock.advance_seconds(1)
    assert cache.get("k") is None

    clock = Clock()
    cache = TTLCache(clock)
    cache.put("k", "v", 10)
    for touch in range(9):                       # last touch sits at +9 s of a 10 s TTL
        clock.advance_seconds(1)
        assert cache.get("k") == "v", f"touch {touch} extended the deadline"
    clock.advance_seconds(1)
    assert cache.get("k") is None


def test_each_entry_expires_on_its_own_without_flushing_the_cache():
    clock = Clock()
    cache = TTLCache(clock)
    cache.put("short", 1, 10)
    cache.put("long", 2, 30)
    cache.put("other", 3, 30)
    clock.advance_seconds(11)
    assert cache.get("short") is None
    assert cache.get("long") == 2
    assert cache.get("other") == 3
    assert len(cache) == 2

    clock = Clock()
    cache = TTLCache(clock)
    cache.put("dead", "x", 5)
    cache.put("alive", "y", 60)
    clock.advance_seconds(5)
    assert cache.get_or_load("dead", lambda: "reloaded", 5) == "reloaded"
    assert cache.get("alive") == "y"


def test_every_falsey_payload_survives_a_hit_and_a_default():
    for value in [False, 0, 0.0, "", [], {}, None]:
        clock = Clock()
        cache = TTLCache(clock)
        cache.put("k", value, 10)
        assert cache.get("k", "fallback") == value, value
        assert cache.get("k") == value or value is None, value
        assert cache.get_or_load("k", boom, 10) == value, value
        assert len(cache) == 1, value


def test_a_miss_still_loads_and_an_expired_entry_is_not_a_hit():
    for value in [False, 0, "", None]:
        clock = Clock()
        cache = TTLCache(clock)
        cache.put("k", value, 5)
        clock.advance_seconds(5)
        calls = []
        assert cache.get_or_load("k", lambda: (calls.append(1), "fresh")[1], 30) == "fresh"
        assert calls == [1], value
        assert cache.get("k") == "fresh", value
        clock.advance_seconds(29.999)
        assert cache.get("k") == "fresh", value
        clock.advance_seconds(0.001)
        assert cache.get("k") is None, value


def test_reput_replaces_the_deadline_and_ttl_bounds_stay_honoured():
    clock = Clock()
    cache = TTLCache(clock)
    cache.put("k", "first", 30)
    cache.put("k", "second", 5)
    clock.advance_seconds(6)
    assert cache.get("k") is None, "the shorter ttl did not replace the longer one"

    clock = Clock()
    cache = TTLCache(clock)
    cache.put("k", "first", 5)
    cache.put("k", "second", 30)
    clock.advance_seconds(6)
    assert cache.get("k") == "second"

    fresh = TTLCache(Clock())
    assert fresh.get("absent") is None
    assert fresh.get("absent", "d") == "d"
    assert len(fresh) == 0
    for bad_ttl in [-1, -0.5]:
        with pytest.raises(ValueError):
            fresh.put("k", "v", bad_ttl)
    zero = TTLCache(Clock())
    zero.put("k", "v", 0)
    assert zero.get("k", "expired") == "expired"


def test_flag_service_honours_its_ttl_and_keys_per_request():
    class Script:
        def __init__(self, values):
            self.values = iter(values)
            self.calls = []

        def fetch(self, user_id, flag):
            self.calls.append((user_id, flag))
            return next(self.values)

    clock = Clock()
    backend = Script([False, True, False])
    service = FlagService(TTLCache(clock), backend, ttl_seconds=30)
    assert service.enabled("u1", "f") is False
    assert service.enabled("u1", "f") is False
    clock.advance_seconds(29)
    assert service.enabled("u1", "f") is False
    clock.advance_seconds(1)
    assert service.enabled("u1", "f") is True
    assert backend.calls == [("u1", "f"), ("u1", "f")]

    clock = Clock()
    backend = Script([True, False, True, False, True])
    service = FlagService(TTLCache(clock), backend, ttl_seconds=1)
    assert service.enabled("u1", "a") is True
    assert service.enabled("u2", "a") is False
    assert service.enabled("u1", "b") is True
    assert service.enabled("u2", "b") is False
    assert len(backend.calls) == 4, "keys are not independent"
    clock.advance_seconds(1)
    assert service.enabled("u1", "a") is True
    assert len(backend.calls) == 5
