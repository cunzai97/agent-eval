import pytest

from reliable.errors import PermanentError, TransientError
from reliable.retry import retry_call


def scripted(outcomes):
    calls = []

    def operation():
        item = outcomes[len(calls)]
        calls.append(item)
        if isinstance(item, Exception):
            raise item
        return item

    return operation, calls


def test_immediate_success_has_no_delay():
    sleeps = []
    op, calls = scripted(["ok"])
    assert retry_call(op, sleep=sleeps.append) == "ok"
    assert calls == ["ok"]
    assert sleeps == []


def test_transient_failures_use_expected_backoff_then_return_value():
    sleeps = []
    op, calls = scripted([TransientError("a"), TransientError("b"), {"ok": True}])
    assert retry_call(op, attempts=4, base_delay=0.5, max_delay=9, sleep=sleeps.append) == {"ok": True}
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


def test_backoff_is_capped():
    sleeps = []
    op, _ = scripted([TransientError("1"), TransientError("2"), TransientError("3"), "ok"])
    assert retry_call(op, attempts=4, base_delay=0.75, max_delay=1.0, sleep=sleeps.append) == "ok"
    assert sleeps == [0.75, 1.0, 1.0]


def test_permanent_error_is_not_retried_or_slept():
    sleeps = []
    op, calls = scripted([PermanentError("bad"), "should-not-run"])
    with pytest.raises(PermanentError, match="bad"):
        retry_call(op, attempts=5, sleep=sleeps.append)
    assert len(calls) == 1
    assert sleeps == []


def test_unknown_exception_is_not_retried():
    sleeps = []
    op, calls = scripted([KeyError("bad"), "should-not-run"])
    with pytest.raises(KeyError):
        retry_call(op, attempts=5, sleep=sleeps.append)
    assert len(calls) == 1
    assert sleeps == []


def test_exhaustion_raises_last_error_without_final_sleep():
    sleeps = []
    op, calls = scripted([TransientError("first"), TransientError("last")])
    with pytest.raises(TransientError, match="last"):
        retry_call(op, attempts=2, base_delay=0.25, sleep=sleeps.append)
    assert len(calls) == 2
    assert sleeps == [0.25]


def test_attempts_must_be_positive_and_never_calls_operation():
    called = []
    with pytest.raises(ValueError):
        retry_call(lambda: called.append(True), attempts=0)
    assert called == []
