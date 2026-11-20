from .longitude import wrapped_delta
from .normalize import normalize_longitude


def longitude_distance(left, right):
    left = normalize_longitude(left)
    right = normalize_longitude(right)
    return wrapped_delta(left, right)


def dist(a, b):
    dx = longitude_distance(a[0], b[0])
    dy = a[1] - b[1]
    return (dx * dx + dy * dy) ** 0.5
