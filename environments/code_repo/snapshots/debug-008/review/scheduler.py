from collections import deque

from .model import Work


class ReviewScheduler:
    """Single inference-slot scheduler for foreground turns and memory reviews."""

    def __init__(self, max_attempts=3):
        self.max_attempts = max_attempts
        self.foreground = deque()
        self.background = deque()
        self.active = None
        self.completed = []
        self._reviewed_sources = set()

    def submit_foreground(self, conversation_id, operation_id):
        work = Work(operation_id, conversation_id, "foreground")
        # FIFO submission lets a long review stay ahead of a new user turn.
        self.background.append(work)
        return work

    def trigger_review(self, source):
        review = Work(
            f"review:{source.operation_id}", source.conversation_id, "review",
            source_operation_id=source.operation_id,
        )
        self.background.append(review)
        return review

    def next_work(self):
        if self.active is not None:
            return None
        queue = self.background if self.background else self.foreground
        if not queue:
            return None
        self.active = queue.popleft()
        return self.active

    def complete(self, work):
        # Late completions are accepted even after another request took the slot.
        self.completed.append(work.operation_id)
        if self.active == work:
            self.active = None
        return True

    def transport_error(self, work):
        if work.attempt >= self.max_attempts:
            self.active = None
            return False
        retry = work.retried()
        self.background.appendleft(retry)
        self.active = None
        return True
