from review.scheduler import ReviewScheduler
from review.transport import TransportAdapter


def finish_foreground(scheduler, conversation="a", operation="turn-1"):
    work = scheduler.submit_foreground(conversation, operation)
    assert scheduler.next_work() == work
    assert scheduler.complete(work)
    return work


def test_review_can_run_after_foreground_becomes_idle():
    scheduler = ReviewScheduler()
    source = finish_foreground(scheduler)
    review = scheduler.trigger_review(source)
    assert scheduler.next_work() == review


def test_foreground_has_priority_over_queued_review():
    scheduler = ReviewScheduler()
    source = finish_foreground(scheduler)
    review = scheduler.trigger_review(source)
    foreground = scheduler.submit_foreground("b", "turn-2")
    assert scheduler.next_work() == foreground
    scheduler.complete(foreground)
    assert scheduler.next_work() == review


def test_new_foreground_supersedes_running_review_and_late_completion_is_ignored():
    scheduler = ReviewScheduler()
    source = finish_foreground(scheduler)
    review = scheduler.trigger_review(source)
    assert scheduler.next_work() == review
    foreground = scheduler.submit_foreground("a", "turn-2")
    assert scheduler.next_work() == foreground
    assert scheduler.complete(review) is False
    assert review.operation_id not in scheduler.completed


def test_review_trigger_is_idempotent_and_never_recursive():
    scheduler = ReviewScheduler()
    source = finish_foreground(scheduler)
    first = scheduler.trigger_review(source)
    assert scheduler.trigger_review(source) is first
    assert scheduler.trigger_review(first) is None
    assert list(scheduler.background) == [first]


def test_transport_has_one_retry_owner_and_preserves_operation_id():
    scheduler = ReviewScheduler(max_attempts=3)
    work = scheduler.submit_foreground("a", "turn-1")
    assert scheduler.next_work() == work
    assert TransportAdapter(scheduler).incomplete_stream(work)
    retry = scheduler.next_work()
    assert retry.operation_id == work.operation_id
    assert retry.attempt == 2
    assert scheduler.next_work() is None


def test_retry_budget_is_total_attempts_not_retries():
    scheduler = ReviewScheduler(max_attempts=2)
    work = scheduler.submit_foreground("a", "turn-1")
    scheduler.next_work()
    assert scheduler.transport_error(work)
    retry = scheduler.next_work()
    assert retry.attempt == 2
    assert scheduler.transport_error(retry) is False
    assert scheduler.next_work() is None


def test_duplicate_completion_is_not_recorded_twice():
    scheduler = ReviewScheduler()
    work = scheduler.submit_foreground("a", "turn-1")
    scheduler.next_work()
    assert scheduler.complete(work)
    assert scheduler.complete(work) is False
    assert scheduler.completed == ["turn-1"]


def test_new_turn_removes_obsolete_review_for_same_conversation_only():
    scheduler = ReviewScheduler()
    a = finish_foreground(scheduler, "a", "a1")
    b = finish_foreground(scheduler, "b", "b1")
    review_a = scheduler.trigger_review(a)
    review_b = scheduler.trigger_review(b)
    foreground = scheduler.submit_foreground("a", "a2")
    assert scheduler.next_work() == foreground
    scheduler.complete(foreground)
    assert scheduler.next_work() == review_b
    assert review_a not in scheduler.background


def test_stale_error_from_superseded_review_cannot_enqueue_retry():
    scheduler = ReviewScheduler()
    source = finish_foreground(scheduler)
    review = scheduler.trigger_review(source)
    scheduler.next_work()
    foreground = scheduler.submit_foreground("a", "turn-2")
    assert scheduler.transport_error(review) is False
    assert scheduler.next_work() == foreground
