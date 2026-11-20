"""Deterministic exponential-backoff policy; no sleeping or wall clock."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    cap_delay: float = 8.0

    def __post_init__(self):
        if not isinstance(self.max_attempts, int) or self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

    def delay(self, attempt):
        """attempt is the one-based attempt number that just failed."""
        if not isinstance(attempt, int) or attempt <= 0:
            raise ValueError("attempt must be positive")
        return min(self.cap_delay, self.base_delay * attempt)
