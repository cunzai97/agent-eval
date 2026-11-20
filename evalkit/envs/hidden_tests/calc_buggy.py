"""Hidden checks for snapshot calc_buggy (task fix-failing-test-001).

Named after the snapshot because `code_hidden.run_hidden_checks` resolves
`hidden_tests/<snapshot>.py`; `fix-001.py` would never be discovered.

What the visible suite leaves open
----------------------------------
`tests/test_math.py` imports only `calc.mathops` and calls only the three
façades (`sum_range`, `weighted_avg`, `clamp`) on hand-picked values. Nothing
in it reaches `calc.bounds.inclusive_stop`, `calc.range_policy.inclusive_values`
or `calc.statistics.weighted_mean`, so a repair that re-derives the right answer
at the call site while leaving the shared helper broken scores 9/9 green. That
bypass also satisfies the task's `files_changed` check, because an edit to
`calc/bounds.py` that merely restates the docstring counts as a change. This
snapshot is the same one locate-bug-001 uses to ask "where is the first
definition point that breaks the inclusive-range contract", so the per-layer
contract is the thing the task is about, not a convention invented here.

The cases therefore attack three gaps:
1. each module's own documented contract, called directly
   (`inclusive_stop` must be one past the end; `weighted_mean` must divide by
   the *sum of weights*, not by the number of pairs);
2. generalization beyond the three pinned inputs, so a closed form or a
   hard-coded sample cannot masquerade as a fix;
3. boundary coverage of the two rejection contracts (reversed bounds,
   zero total weight) over inputs the visible tests never try.

Deliberate absences, because the prompt does not settle them and the historical
passing population genuinely disagrees or is silent:
- no `pairs` is ever passed as an *iterator*: implementations that read the
  weights and the products in one pass and those that traverse twice are both
  faithful to "加权平均", and measured across the 15 distinct passing
  implementations of this task the same generator input returns 17.5, 0.0 or
  raises. Only sequences are used.
- no case has a *negative* total weight (e.g. `[(10, 1), (20, -3)]`). "拒绝零权重"
  pins the zero denominator; whether a negative denominator is refused or simply
  divided through is not stated anywhere, and every historical implementation
  would be graded on a coin flip.
- nothing asserts a result *type*, an exception message, or rounding mode.
  `Decimal(...)/int == 17.5` compares equal to the float, so an exact-arithmetic
  fix stays welcome.
- `clamp` on a degenerate range (`lo == hi`) is asserted to be a value, not an
  error, since it is not a reversed range; that is the near-universal reading of
  "反向边界" and all 15 implementations agree on it.
"""
import pytest

from calc.bounds import inclusive_stop
from calc.mathops import clamp, sum_range, weighted_avg
from calc.range_policy import inclusive_values
from calc.statistics import weighted_mean


# --- layer 1: calc/bounds.py is where the inclusive contract is introduced ----

def test_inclusive_stop_is_one_past_the_inclusive_end():
    """The defect's definition point, tested without the call sites that hide it."""
    assert inclusive_stop(5) == 6
    assert inclusive_stop(0) == 1
    assert inclusive_stop(1) == 2
    assert inclusive_stop(10) == 11
    assert inclusive_stop(-3) == -2


def test_inclusive_stop_is_the_translator_range_needs():
    for end in (0, 1, 4, 20):
        assert list(range(1, inclusive_stop(end))) == list(range(1, end + 1))


# --- layer 2: calc/range_policy.py keeps the endpoint --------------------------

def test_inclusive_values_yields_every_endpoint():
    assert list(inclusive_values(1, 5)) == [1, 2, 3, 4, 5]
    assert list(inclusive_values(3, 3)) == [3]
    assert list(inclusive_values(7, 7)) == [7]
    assert list(inclusive_values(0, 4)) == [0, 1, 2, 3, 4]
    assert list(inclusive_values(-3, -1)) == [-3, -2, -1]
    assert list(inclusive_values(1, 2)) == [1, 2]


def test_inclusive_values_on_an_empty_span_yields_no_values():
    for start, end in ((4, 2), (2, 1), (5, 4), (1, 0), (10, 9)):
        assert list(inclusive_values(start, end)) == []
    assert sum(inclusive_values(1, 0)) == 0


# --- the sum façade, generalized past the three pinned samples -----------------

@pytest.mark.parametrize("n, want", [
    (2, 3), (3, 6), (6, 21), (7, 28), (10, 55), (15, 120), (20, 210), (100, 5050),
])
def test_sum_range_counts_the_last_term_for_unseen_inputs(n, want):
    assert sum_range(n) == want


def test_sum_range_boundary_and_negative_input():
    assert sum_range(0) == 0
    assert sum_range(1) == 1
    for n in (-1, -2, -100):
        with pytest.raises(ValueError):
            sum_range(n)


def test_sum_range_agrees_with_its_own_iterator():
    for n in (0, 1, 2, 5, 12):
        assert sum_range(n) == sum(inclusive_values(1, n))


# --- layer 3: calc/statistics.py denominator contract -------------------------

def test_weighted_mean_divides_by_total_weight_not_pair_count():
    """The other definition point the visible tests only reach through a façade."""
    assert weighted_mean([(10, 1), (20, 3)]) == 17.5
    assert weighted_mean([(3, 1), (6, 2), (9, 3)]) == 7.0
    assert weighted_mean([(7, 3)]) == 7.0
    assert weighted_mean([(2, 4)]) == 2.0
    assert weighted_mean([(1, 1), (1, 2), (1, 3)]) == 1.0
    assert weighted_mean([(100, 10), (200, 10), (300, 10)]) == 200.0
    assert weighted_mean([(-10, 1), (10, 1), (30, 2)]) == 15.0


def test_weighted_mean_zero_weight_and_empty_input():
    assert weighted_mean([]) == 0.0
    assert weighted_mean([(0, 5), (0, 3)]) == 0.0
    for pairs in ([(10, 1), (20, -1)], [(5, 0), (7, 0)], [(1, 2), (2, -2)]):
        with pytest.raises(ValueError):
            weighted_mean(pairs)


# --- the weighted-average façade ----------------------------------------------

def test_weighted_avg_uses_the_weight_denominator():
    assert weighted_avg([(3, 1), (6, 2), (9, 3)]) == 7.0
    assert weighted_avg([(100, 10), (200, 10), (300, 10)]) == 200.0
    assert weighted_avg([(-10, 1), (10, 1), (30, 2)]) == 15.0
    assert weighted_avg([(10, 1), (20, 3), (0, 0)]) == 17.5
    assert weighted_avg([(0, 5), (0, 3)]) == 0.0


def test_weighted_avg_with_weights_that_do_not_sum_to_the_count():
    assert weighted_avg([(10, 2), (20, -1)]) == 0.0
    assert weighted_avg([(5, -1), (5, 2)]) == 5.0
    assert weighted_avg([(1, 0.5), (2, 0.25)]) == pytest.approx(1.3333333333333333, rel=1e-12)


def test_weighted_avg_empty_and_zero_total_weight():
    assert weighted_avg([]) == 0.0
    for pairs in ([(10, 1), (20, -1)], [(5, 0), (7, 0)]):
        with pytest.raises(ValueError):
            weighted_avg(pairs)


# --- clamp: rejection is a property of the bounds, not of x -------------------

@pytest.mark.parametrize("x", [-5, 0, 1, 5, 9, 10, 50, 1000])
def test_clamp_refuses_a_reversed_range_whatever_the_value_is(x):
    with pytest.raises(ValueError):
        clamp(x, 10, 0)


def test_clamp_refuses_reversed_ranges_beyond_the_sample():
    for lo, hi in ((0, -1), (100, -100), (3.5, 3.4), (10, 9)):
        with pytest.raises(ValueError):
            clamp(5, lo, hi)


def test_clamp_keeps_both_endpoints_and_neutral_values():
    assert clamp(-1, 0, 10) == 0
    assert clamp(0, 0, 10) == 0
    assert clamp(5, 0, 10) == 5
    assert clamp(10, 0, 10) == 10
    assert clamp(11, 0, 10) == 10
    assert clamp(-5, -10, -1) == -5
    assert clamp(-20, -10, -1) == -10
    assert clamp(-1, -5, -2) == -2


def test_clamp_does_not_invent_a_reversed_range_for_a_degenerate_one():
    assert clamp(5, 3, 3) == 3
    assert clamp(2, 3, 3) == 3
    assert clamp(9, 10, 10) == 10
    assert clamp(0, 0, 0) == 0
    assert clamp(-1, -1, -1) == -1


def test_clamp_works_on_values_between_non_integer_bounds():
    assert clamp(2.5, 0, 3) == 2.5
    assert clamp(10.5, 0, 10) == 10
    assert clamp(-0.5, -1, 1) == -0.5


# --- the prompt's "keep the public signatures" contract -----------------------

def test_public_signatures_still_accept_keyword_calls():
    assert sum_range(n=5) == 15
    assert weighted_avg(pairs=[(10, 1), (20, 3)]) == 17.5
    assert clamp(x=5, lo=0, hi=10) == 5
