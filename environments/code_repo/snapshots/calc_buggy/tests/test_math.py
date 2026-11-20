import pytest

from calc.mathops import clamp, sum_range, weighted_avg


def test_sum_range_is_inclusive():
    assert sum_range(5) == 15
    assert sum_range(1) == 1


def test_sum_range_zero_is_empty():
    assert sum_range(0) == 0


def test_sum_range_rejects_negative_input():
    with pytest.raises(ValueError):
        sum_range(-1)


def test_weighted_average_uses_sum_of_weights():
    assert weighted_avg([(10, 1), (20, 3)]) == 17.5


def test_weighted_average_empty_is_zero():
    assert weighted_avg([]) == 0.0


def test_weighted_average_rejects_zero_total_weight():
    with pytest.raises(ValueError):
        weighted_avg([(10, 1), (20, -1)])


def test_clamp_boundaries():
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10


def test_clamp_rejects_reversed_bounds():
    with pytest.raises(ValueError):
        clamp(5, 10, 0)


def test_clamp_keeps_value_inside_range():
    assert clamp(5, 0, 10) == 5
