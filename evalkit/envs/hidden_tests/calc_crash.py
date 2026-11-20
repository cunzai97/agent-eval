"""Hidden checks for snapshot calc_crash.

The visible suite pins exactly one number per rule (total 100, rates 0.1 / 0.5 /
"10%", one cap of 15, one void line).  It therefore stays green for a fix that
hard-codes those shapes: `if rate == "10%"`, `discount = min(discount, cap)`
written so a bigger cap wins, a `qty` default that only kicks in for the first
item, or validation living entirely inside `apply_coupon` while
`calc.validation.coupon_rate` is left as it was.

These checks re-drive the same two layers that the task prompt promises to
repair -- `validation.coupon_rate` reached through `apply_coupon`, and
`subtotal` -- with numbers the visible suite never uses:

* rates accepted as a 0..1 float *and* as a percent string, at several scales;
* a cap that must only ever *shrink* the discount (so a cap above the discount
  leaves it alone, a falsey cap of 0 still clamps, and a discount is never
  larger than the order total);
* every invalid rate form still rejected, including a percent string out of
  range and a rate a cap would otherwise have hidden;
* no coupon at all -- key absent or explicitly `None`, which is exactly what the
  existing call site `coupon_rate(order.get("coupon"))` passes on every coupon
  free order -- leaves the total untouched;
* subtotal: default qty, void lines skipped, non-void statuses kept, qty 0 kept,
  a negative qty rejected wherever it sits, and no mutation of the caller's
  item dicts or order.

Calibration note: whether `coupon_rate({"rate": None})` answers `ValueError` or
`TypeError` is *not* asserted.  Requiring `ValueError` there measured 27/34
deaths on replay -- the whole passing population parses with `float(value)`, so
that clause would have graded an exception class instead of a defect.  What is
asserted is that an unusable rate fails loudly rather than silently becoming 0.

Anything else the prompt does not state (a bare numeric string such as "0.2" as
a rate, padded percent strings, `coupon: {}`, case variants of the `void`
status, a negative qty on a void line) is deliberately left unasserted.
"""
import pathlib
import shutil

# The graded workspace carries __pycache__ written by the model's own run_tests
# calls.  CPython re-uses a cached .pyc while (source mtime-second, size) are
# unchanged, which is exactly what a `>` -> `>=` edit paired with a `<=` -> `<`
# edit produces, so cached bytecode can lag the source.  Drop it: these checks
# must read the code as written.
for _stale in pathlib.Path(__file__).resolve().parent.rglob("__pycache__"):
    shutil.rmtree(_stale, ignore_errors=True)

import pytest

from calc.coupon import apply_coupon, subtotal
from calc.validation import coupon_rate


# --------------------------------------------------------------------------
# coupon rate: fraction in 0..1 and percent string
# --------------------------------------------------------------------------
@pytest.mark.parametrize("total, rate, expected", [
    (200, 0.25, 150.0),
    (80, 0.05, 76.0),
    (70, 0.3, 49.0),
    (240, "25%", 180.0),
    (80, "12.5%", 70.0),
    (250, "7%", 232.5),
    (123.45, "10%", 111.105),
])
def test_rate_accepted_as_fraction_and_as_percent_string(total, rate, expected):
    assert apply_coupon({"total": total, "coupon": {"rate": rate}}) == pytest.approx(expected)


def test_same_rate_as_string_and_as_float_agree():
    stringy = apply_coupon({"total": 320, "coupon": {"rate": "15%"}})
    numeric = apply_coupon({"total": 320, "coupon": {"rate": 0.15}})
    assert stringy == pytest.approx(numeric) == pytest.approx(272.0)


# --------------------------------------------------------------------------
# no coupon at all: no KeyError, total untouched
# --------------------------------------------------------------------------
def test_order_without_a_coupon_key_is_untouched():
    assert apply_coupon({"total": 240}) == 240
    assert apply_coupon({"total": 33.5}) == 33.5


def test_explicit_null_coupon_is_untouched():
    assert apply_coupon({"total": 60, "coupon": None}) == 60
    assert apply_coupon({"total": 250.5, "coupon": None}) == 250.5


def test_a_rate_of_zero_is_no_discount():
    assert apply_coupon({"total": 100, "coupon": {"rate": 0}}) == 100
    assert apply_coupon({"total": 100, "coupon": {"rate": "0%"}}) == 100


# --------------------------------------------------------------------------
# cap direction: the cap bounds the discount, never the other way round
# --------------------------------------------------------------------------
def test_cap_below_discount_reduces_the_discount():
    order = {"total": 120, "coupon": {"rate": 0.4, "max_discount": 30}}
    assert apply_coupon(order) == pytest.approx(90.0)


def test_cap_above_discount_is_not_applied():
    order = {"total": 60, "coupon": {"rate": 0.5, "max_discount": 90}}
    assert apply_coupon(order) == pytest.approx(30.0)


def test_zero_cap_means_no_discount():
    order = {"total": 100, "coupon": {"rate": "20%", "max_discount": 0}}
    assert apply_coupon(order) == pytest.approx(100.0)


def test_absent_cap_is_not_a_zero_cap():
    assert apply_coupon({"total": 100, "coupon": {"rate": 0.5, "max_discount": None}}) == pytest.approx(50.0)
    assert apply_coupon({"total": 90, "coupon": {"rate": "50%", "max_discount": 1000}}) == pytest.approx(45.0)


@pytest.mark.parametrize("total, rate, cap, expected", [
    (137.5, "30%", 10, 127.5),
    (200, 0.9, 45, 155.0),
    (90, "50%", 1000, 45.0),
    (75, 0.2, 7, 68.0),
])
def test_discount_never_exceeds_the_order_total(total, rate, cap, expected):
    paid = apply_coupon({"total": total, "coupon": {"rate": rate, "max_discount": cap}})
    assert paid == pytest.approx(expected)
    assert 0.0 <= paid <= total


# --------------------------------------------------------------------------
# invalid rates are rejected, whichever branch parses them
# --------------------------------------------------------------------------
@pytest.mark.parametrize("rate", [-0.5, 1.4, "not-a-rate", "-10%", "110%", "10", "abc"])
def test_out_of_range_and_unparsable_rates_raise_value_error(rate):
    with pytest.raises(ValueError):
        apply_coupon({"total": 100, "coupon": {"rate": rate}})


def test_a_cap_cannot_mask_an_unusable_rate():
    with pytest.raises(ValueError):
        apply_coupon({"total": 100, "coupon": {"rate": 5, "max_discount": 10}})


def test_validation_layer_owns_the_rate_rules():
    assert coupon_rate(None) == 0.0
    assert coupon_rate({"rate": 0}) == 0.0
    assert coupon_rate({"rate": 1}) == 1.0
    assert coupon_rate({"rate": 0.1}) == pytest.approx(0.1)
    assert coupon_rate({"rate": "25%"}) == pytest.approx(0.25)
    assert coupon_rate({"rate": "100%"}) == 1.0
    for bad in [-0.5, 1.4, "not-a-rate", "110%", "abc"]:
        with pytest.raises(ValueError):
            coupon_rate({"rate": bad})


def test_a_rate_that_is_not_a_number_fails_loudly_rather_than_becoming_zero():
    for unusable in [None, [0.1], {"n": 1}]:
        with pytest.raises(Exception) as caught:
            coupon_rate({"rate": unusable})
        assert not isinstance(caught.value, (KeyError, IndexError)), unusable
        with pytest.raises(Exception):
            apply_coupon({"total": 100, "coupon": {"rate": unusable}})


# --------------------------------------------------------------------------
# subtotal: default qty, void skipping, negative qty rejection
# --------------------------------------------------------------------------
def test_subtotal_defaults_qty_skips_void_and_keeps_other_statuses():
    items = [
        {"price": 12.5},                                  # qty defaults to 1
        {"price": 4, "qty": 3},                           # 12
        {"price": 100, "qty": 2, "status": "void"},       # skipped
        {"price": 7, "qty": 0},                           # 0, but a real line
        {"price": 2, "qty": 5, "status": "active"},       # 10, not void
        {"price": 0.5, "qty": 4},                         # 2
    ]
    assert subtotal(items) == pytest.approx(36.5)


def test_subtotal_of_only_void_lines_is_zero():
    assert subtotal([{"price": 9, "qty": 2, "status": "void"}]) == 0
    assert subtotal([]) == 0


def test_void_line_without_a_qty_is_still_skipped():
    assert subtotal([{"price": 999, "status": "void"}, {"price": 4}]) == 4
    assert subtotal([{"price": 1}, {"price": 2}, {"price": 3}]) == 6


def test_subtotal_consumes_nothing_it_was_given():
    items = [{"price": 12.5}, {"price": 4, "qty": 3}]
    assert subtotal(items) == pytest.approx(24.5)
    assert subtotal(items) == pytest.approx(24.5)
    assert items == [{"price": 12.5}, {"price": 4, "qty": 3}]
    assert subtotal(({"price": 10, "qty": 2},)) == 20


def test_subtotal_rejects_a_negative_qty_anywhere_in_the_list():
    with pytest.raises(ValueError):
        subtotal([{"price": 5, "qty": 2}, {"price": 5, "qty": -3}])
    with pytest.raises(ValueError):
        subtotal([{"price": 10, "qty": -0.5}])
    with pytest.raises(ValueError):
        subtotal([{"price": 3}, {"price": 4, "qty": -1}, {"price": 9}])


def test_apply_coupon_does_not_rewrite_the_order_it_was_given():
    order = {"total": 100, "coupon": {"rate": "50%", "max_discount": 40}}
    assert apply_coupon(order) == pytest.approx(60.0)
    assert order == {"total": 100, "coupon": {"rate": "50%", "max_discount": 40}}
