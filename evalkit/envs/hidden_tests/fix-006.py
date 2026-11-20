"""Hidden checks for snapshot fix-006.

The visible suite pins one script per rule, so an implementation that special
cases those seven shapes stays green while the retry contract is still wrong.
These checks re-run every rule with different shapes: five kinds of
non-transient exception, a permanent error in the middle of a transient
sequence, attempt counts from 1 to 10, an exhaustive falsy/identity sweep of
return values, a doubling curve that runs into the cap, two calls that must not
share state, and illegal attempt budgets. Sleeps are counted through the
injected ``sleep`` callable, so nothing here waits in wall-clock time, and the
delays are whole numbers so exact list equality cannot be broken by float
drift in an implementation that doubles by addition instead of exponentiation.

Deliberately not asserted, because the prompt is silent: whether a subclass of
``TransientError`` counts as transient, whether ``sleep`` may be called with a
zero delay, and whether a non-integer ``attempts`` must raise ``ValueError``
specifically - a TypeError raised before the operation runs is a rejection too,
so both are accepted here.
"""
import pytest

from reliable.backoff import delay_for
from reliable.errors import PermanentError, TransientError
from reliable.retry import retry_call


class NotRetryable(Exception):
    """Deliberately not a TransientError."""


class Exploded(RuntimeError):
    """A RuntimeError that is not transient either."""


def scripted(outcomes, limit=14):
    """Operation that replays ``outcomes``: exception items raise, others return."""
    calls = []

    def operation():
        index = len(calls)
        calls.append(index)
        if index > limit:
            raise AssertionError("operation called far too many times")
        item = outcomes[index] if index < len(outcomes) else outcomes[-1]
        if isinstance(item, BaseException):
            raise item
        return item

    return operation, calls


@pytest.mark.parametrize("error", [
    PermanentError("boom"),
    KeyError("missing"),
    NotRetryable("nope"),
    ZeroDivisionError("divide"),
    Exploded("runtime but not transient"),
])
def test_anything_that_is_not_transient_escapes_on_the_first_attempt(error):
    operation, calls = scripted([error, "tail", "tail"])
    sleeps = []
    with pytest.raises(BaseException) as info:
        retry_call(operation, attempts=6, base_delay=1, max_delay=32, sleep=sleeps.append)
    assert info.value is error, "the original exception object must propagate unchanged"
    assert type(info.value) is type(error)
    assert calls == [0], f"only one attempt is allowed, saw {len(calls)}"
    assert sleeps == [], "a non-transient failure must not be followed by a delay"


def test_a_permanent_error_between_transient_ones_ends_the_sequence():
    stop = PermanentError("stop")
    operation, calls = scripted([TransientError("a"), stop, "tail", "tail"])
    sleeps = []
    with pytest.raises(PermanentError) as info:
        retry_call(operation, attempts=5, base_delay=1, max_delay=8, sleep=sleeps.append)
    assert info.value is stop
    assert calls == [0, 1], f"expected exactly two attempts, saw {len(calls)}"
    assert sleeps == [1]


def test_attempts_is_every_call_including_the_first_one():
    errors = [TransientError("one"), TransientError("two"), TransientError("three")]
    operation, calls = scripted(errors)
    sleeps = []
    with pytest.raises(TransientError) as info:
        retry_call(operation, attempts=3, base_delay=2, max_delay=64, sleep=sleeps.append)
    assert calls == [0, 1, 2], f"3 attempts means 3 calls, saw {len(calls)}"
    assert sleeps == [2, 4], "the third failure must not be followed by a sleep"
    assert info.value is errors[-1], "exhaustion re-raises the last exception"
    assert info.value.args == ("three",)


@pytest.mark.parametrize("attempts", [1, 2, 3, 4, 5, 6])
def test_no_sleep_follows_the_last_failure_of_the_last_attempt(attempts):
    expected = [min(4, 2 ** index) for index in range(attempts - 1)]
    operation, calls = scripted([TransientError("x")])
    sleeps = []
    with pytest.raises(TransientError):
        retry_call(operation, attempts=attempts, base_delay=1, max_delay=4, sleep=sleeps.append)
    assert len(calls) == attempts
    assert len(sleeps) == attempts - 1, f"{attempts} attempts allow {attempts - 1} sleeps"
    assert sleeps == expected


def test_the_curve_starts_at_base_delay_doubles_and_stays_under_the_cap():
    operation, _ = scripted([TransientError("x")])
    sleeps = []
    with pytest.raises(TransientError):
        retry_call(operation, attempts=7, base_delay=2, max_delay=20, sleep=sleeps.append)
    assert sleeps == [2, 4, 8, 16, 20, 20]

    tight, _ = scripted([TransientError("x")])
    capped = []
    with pytest.raises(TransientError):
        retry_call(tight, attempts=3, base_delay=9, max_delay=4, sleep=capped.append)
    assert capped == [4, 4], "the first retry already respects max_delay"


def test_documented_backoff_helper_uses_one_based_retry_numbers():
    assert [delay_for(number, 1, 100) for number in (1, 2, 3, 4)] == [1, 2, 4, 8]
    assert delay_for(9, 1, 20) == 20


@pytest.mark.parametrize("attempts", [0, -1, -99])
def test_a_non_positive_attempt_budget_is_refused_before_any_call(attempts):
    operation, calls = scripted(["never"])
    sleeps = []
    with pytest.raises(ValueError):
        retry_call(operation, attempts=attempts, sleep=sleeps.append)
    assert calls == []
    assert sleeps == []


@pytest.mark.parametrize("attempts", ["3", 2.5, None, [3]])
def test_an_attempts_value_that_is_not_a_count_is_refused_before_any_call(attempts):
    operation, calls = scripted(["never"])
    sleeps = []
    with pytest.raises((ValueError, TypeError)):
        retry_call(operation, attempts=attempts, sleep=sleeps.append)
    assert calls == [], "an illegal budget must not run the operation"
    assert sleeps == []


@pytest.mark.parametrize("value", [None, 0, "", [], False, 0.0, {"nested": None}, object()])
def test_whatever_the_operation_returns_comes_back_untouched(value):
    operation, calls = scripted([TransientError("flaky"), value])
    sleeps = []
    assert retry_call(operation, attempts=4, base_delay=3, max_delay=9, sleep=sleeps.append) is value
    assert calls == [0, 1], "a successful call ends the sequence, however falsy it looks"
    assert sleeps == [3]


def test_success_on_the_very_first_attempt_never_delays():
    marker = object()
    operation, calls = scripted([marker])
    sleeps = []
    assert retry_call(operation, attempts=5, base_delay=1, max_delay=8, sleep=sleeps.append) is marker
    assert calls == [0]
    assert sleeps == []


def test_the_default_attempt_budget_is_three_total_calls():
    operation, calls = scripted([TransientError("x")])
    sleeps = []
    with pytest.raises(TransientError):
        retry_call(operation, base_delay=1, max_delay=64, sleep=sleeps.append)
    assert calls == [0, 1, 2]
    assert sleeps == [1, 2]


def test_a_long_run_of_transient_failures_wears_the_budget_out_exactly():
    errors = [TransientError(f"fail{index}") for index in range(10)]
    operation, calls = scripted(errors, limit=24)
    sleeps = []
    with pytest.raises(TransientError) as info:
        retry_call(operation, attempts=10, base_delay=1, max_delay=16, sleep=sleeps.append)
    assert len(calls) == 10, f"expected exactly 10 calls, saw {len(calls)}"
    assert len(sleeps) == 9, "the tenth failure is the last one: no delay after it"
    assert sleeps == [1, 2, 4, 8, 16, 16, 16, 16, 16]
    assert info.value is errors[-1], "the exception that escapes is the one from the last attempt"
    assert info.value not in errors[:-1]


def test_equal_failures_still_end_with_their_own_exception_object():
    first, second = TransientError("same"), TransientError("same")
    operation, calls = scripted([first, second, first])
    sleeps = []
    with pytest.raises(TransientError) as info:
        retry_call(operation, attempts=2, base_delay=5, max_delay=5, sleep=sleeps.append)
    assert calls == [0, 1]
    assert sleeps == [5]
    assert info.value is second


def test_two_back_to_back_calls_keep_their_own_attempts_and_their_own_error():
    sleeps = []
    first = TransientError("first call")
    second = TransientError("second call")
    marker = object()

    first_op, first_calls = scripted([first, first, first])
    with pytest.raises(TransientError) as info:
        retry_call(first_op, attempts=3, base_delay=1, max_delay=8, sleep=sleeps.append)
    assert info.value is first
    assert len(first_calls) == 3
    assert len(sleeps) == 2

    second_op, second_calls = scripted([second, marker])
    assert retry_call(second_op, attempts=3, base_delay=1, max_delay=8, sleep=sleeps.append) is marker
    assert len(second_calls) == 2, "the second call starts from a clean attempt counter"
    assert len(sleeps) == 3, "only the delays of the first call plus the one retry of this call"
