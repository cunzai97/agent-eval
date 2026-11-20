from .range_policy import inclusive_values
from .statistics import weighted_mean


def sum_range(n):
    if n < 0:
        raise ValueError("n must be non-negative")
    return sum(inclusive_values(1, n))


def weighted_avg(pairs):
    return weighted_mean(pairs)


def clamp(x, lo, hi):
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(hi, x))
