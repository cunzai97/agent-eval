import math

import pytest

from retrykit import RetryPolicy, RetryScheduler


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3"])
def test_max_attempts_validation(value):
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=value)


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, True, "1"])
def test_base_delay_validation(value):
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=value)


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, True, "1"])
def test_cap_delay_validation(value):
    with pytest.raises(ValueError):
        RetryPolicy(cap_delay=value)


def test_cap_must_cover_base_delay():
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=3, cap_delay=2)


@pytest.mark.parametrize("attempt", [0, -1, True, 1.5, "1"])
def test_delay_attempt_validation(attempt):
    with pytest.raises(ValueError):
        RetryPolicy().delay(attempt)


def test_large_backoff_is_capped_without_overflow():
    assert RetryPolicy(max_attempts=3000, base_delay=2, cap_delay=100).delay(2000) == 100


@pytest.mark.parametrize("operation_id", ["", "  ", None, 3])
def test_operation_id_validation(operation_id):
    with pytest.raises(ValueError):
        RetryScheduler().submit(operation_id, None)


@pytest.mark.parametrize("now", [math.nan, math.inf, -math.inf, True, "0"])
def test_submit_time_validation(now):
    with pytest.raises(ValueError):
        RetryScheduler().submit("op", None, now=now)


@pytest.mark.parametrize("now", [math.nan, math.inf, True, "0"])
def test_start_time_validation(now):
    with pytest.raises(ValueError):
        RetryScheduler().start(now)


@pytest.mark.parametrize("now", [math.nan, math.inf, True, "0"])
def test_fail_time_validation(now):
    scheduler = RetryScheduler()
    token = scheduler.submit("op", None)
    scheduler.start(0)
    with pytest.raises(ValueError):
        scheduler.fail("op", token, now)


def test_due_then_id_ordering():
    scheduler = RetryScheduler()
    scheduler.submit("z", None, now=1)
    scheduler.submit("a", None, now=1)
    scheduler.submit("b", None, now=0)
    assert [scheduler.start(1)["id"] for _ in range(3)] == ["b", "a", "z"]


def test_no_ready_operation_returns_none():
    scheduler = RetryScheduler()
    scheduler.submit("op", None, now=10)
    assert scheduler.start(9) is None


def test_retry_delay_uses_failed_attempt_number():
    scheduler = RetryScheduler(RetryPolicy(max_attempts=4, base_delay=2, cap_delay=20))
    first = scheduler.submit("op", None)
    scheduler.start(0)
    assert scheduler.fail("op", first, 5) == "retry"
    assert scheduler.start(6) is None
    second = scheduler.start(7)
    assert second["attempts"] == 1
    assert scheduler.fail("op", second["token"], 10) == "retry"
    assert scheduler.start(13) is None
    assert scheduler.start(14)["attempts"] == 2


def test_stale_fail_does_not_change_snapshot():
    scheduler = RetryScheduler()
    token = scheduler.submit("op", None)
    scheduler.start(0)
    assert scheduler.fail("op", token + 1, 0) == "stale"
    assert scheduler.snapshot() == [{"id": "op", "state": "running", "attempts": 0, "due": 0.0, "token": 1}]


def test_stale_success_does_not_complete_retried_operation():
    scheduler = RetryScheduler()
    old = scheduler.submit("op", None)
    scheduler.start(0)
    scheduler.fail("op", old, 0)
    assert not scheduler.succeed("op", old)
    assert scheduler.snapshot()[0]["state"] == "ready"


def test_cancel_ready_and_running():
    scheduler = RetryScheduler()
    ready = scheduler.submit("a", None)
    running = scheduler.submit("b", None)
    assert scheduler.cancel("a")
    scheduler.start(0)
    assert scheduler.cancel("b")
    assert scheduler.fail("b", running, 0) == "stale"
    assert not scheduler.cancel("a")
    assert scheduler.start(0) is None


def test_terminal_callbacks_are_stale_or_false():
    scheduler = RetryScheduler(RetryPolicy(max_attempts=1))
    token = scheduler.submit("op", None)
    scheduler.start(0)
    assert scheduler.fail("op", token, 0) == "failed"
    assert scheduler.fail("op", token, 0) == "stale"
    assert not scheduler.succeed("op", token)
    assert not scheduler.cancel("op")


def test_snapshot_is_sorted_and_has_public_fields_only():
    scheduler = RetryScheduler()
    scheduler.submit("z", {"secret": 1})
    scheduler.submit("a", None)
    snap = scheduler.snapshot()
    assert [row["id"] for row in snap] == ["a", "z"]
    assert all(set(row) == {"id", "state", "attempts", "due", "token"} for row in snap)


def test_success_is_terminal_and_due_is_cleared():
    scheduler = RetryScheduler()
    token = scheduler.submit("op", None)
    scheduler.start(0)
    assert scheduler.succeed("op", token)
    assert scheduler.snapshot()[0]["due"] is None
    assert scheduler.start(1000) is None


def test_default_policy_three_attempts():
    scheduler = RetryScheduler()
    token = scheduler.submit("op", None)
    outcomes = []
    for now in (0, 1, 3):
        current = scheduler.start(now)
        assert current["token"] == token
        outcomes.append(scheduler.fail("op", token, now))
        token += 1
    assert outcomes == ["retry", "retry", "failed"]
    assert scheduler.snapshot()[0]["attempts"] == 3
