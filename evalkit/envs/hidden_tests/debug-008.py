from review.scheduler import ReviewScheduler
from review.transport import TransportAdapter


def completed_turn(scheduler, conversation, operation):
    work = scheduler.submit_foreground(conversation, operation)
    assert scheduler.next_work() == work
    assert scheduler.complete(work)
    return work


def test_old_attempt_completion_is_stale_after_retry_dispatch():
    scheduler = ReviewScheduler(max_attempts=3)
    first = scheduler.submit_foreground("c", "op")
    scheduler.next_work()
    assert scheduler.transport_error(first)
    retry = scheduler.next_work()
    assert retry.attempt == 2
    assert scheduler.complete(first) is False
    assert scheduler.active == retry
    assert scheduler.complete(retry)
    assert scheduler.completed == ["op"]


def test_foreground_does_not_preempt_an_active_foreground():
    scheduler = ReviewScheduler()
    first = scheduler.submit_foreground("a", "a1")
    assert scheduler.next_work() == first
    second = scheduler.submit_foreground("b", "b1")
    assert scheduler.active == first
    assert scheduler.next_work() is None
    scheduler.complete(first)
    assert scheduler.next_work() == second


def test_transport_adapter_never_creates_duplicate_retries():
    scheduler = ReviewScheduler(max_attempts=4)
    work = scheduler.submit_foreground("a", "a1")
    scheduler.next_work()
    adapter = TransportAdapter(scheduler)
    assert adapter.incomplete_stream(work)
    assert len(scheduler.foreground) + len(scheduler.background) == 1


def test_recursive_review_rejection_does_not_poison_source_dedupe():
    scheduler = ReviewScheduler()
    source = completed_turn(scheduler, "a", "a1")
    review = scheduler.trigger_review(source)
    assert scheduler.trigger_review(review) is None
    assert scheduler.trigger_review(source) is review


def test_other_conversation_review_survives_foreground_preemption():
    scheduler = ReviewScheduler()
    source_b = completed_turn(scheduler, "b", "b1")
    review_b = scheduler.trigger_review(source_b)
    assert scheduler.next_work() == review_b
    foreground_a = scheduler.submit_foreground("a", "a1")
    assert scheduler.next_work() == foreground_a
    assert scheduler.complete(review_b) is False
    scheduler.complete(foreground_a)
    # The interrupted review belongs to another conversation and is resumed once.
    resumed = scheduler.next_work()
    assert resumed is not None
    assert resumed.operation_id == review_b.operation_id
