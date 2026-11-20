import pytest

from retrykit import RetryPolicy, RetryScheduler


def test_policy_uses_exponential_backoff_and_cap():
    policy = RetryPolicy(max_attempts=5, base_delay=2, cap_delay=7)
    assert [policy.delay(n) for n in (1, 2, 3, 4)] == [2, 4, 7, 7]


def test_policy_rejects_invalid_delays():
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=4, cap_delay=2)


def test_submit_and_start_return_attempt_context():
    scheduler = RetryScheduler()
    token = scheduler.submit(" op ", {"x": 1}, now=3)
    assert token == 1
    assert scheduler.start(2) is None
    assert scheduler.start(3) == {"id": "op", "payload": {"x": 1}, "attempts": 0, "token": 1}


def test_ready_order_uses_due_then_id():
    scheduler = RetryScheduler()
    scheduler.submit("b", None, now=5)
    scheduler.submit("a", None, now=1)
    assert scheduler.start(5)["id"] == "a"


def test_retry_rotates_token_and_rejects_stale_success():
    scheduler = RetryScheduler()
    old = scheduler.submit("op", None)
    scheduler.start(0)
    assert scheduler.fail("op", old, now=10) == "retry"
    assert not scheduler.succeed("op", old)
    current = scheduler.start(11)
    assert current["token"] != old
    assert scheduler.succeed("op", current["token"])


def test_attempt_exhaustion_is_terminal():
    scheduler = RetryScheduler(RetryPolicy(max_attempts=1))
    token = scheduler.submit("op", None)
    scheduler.start(0)
    assert scheduler.fail("op", token, 0) == "failed"
    assert scheduler.start(100) is None


def test_cancel_invalidates_running_callback():
    scheduler = RetryScheduler()
    token = scheduler.submit("op", None)
    scheduler.start(0)
    assert scheduler.cancel("op")
    assert not scheduler.succeed("op", token)
    assert scheduler.snapshot()[0]["state"] == "cancelled"


def test_duplicate_id_is_rejected_even_after_success():
    scheduler = RetryScheduler()
    token = scheduler.submit("op", None)
    scheduler.start(0)
    assert scheduler.succeed("op", token)
    with pytest.raises(ValueError):
        scheduler.submit("op", None)
