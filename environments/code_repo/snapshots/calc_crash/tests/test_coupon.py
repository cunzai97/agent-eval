import pytest

from calc.coupon import apply_coupon, subtotal


def test_no_coupon_returns_original_total():
    assert apply_coupon({"total": 100}) == 100


def test_decimal_rate():
    assert apply_coupon({"total": 100, "coupon": {"rate": 0.1}}) == 90


def test_percentage_string_rate():
    assert apply_coupon({"total": 100, "coupon": {"rate": "10%"}}) == 90


def test_discount_cap_limits_discount_not_final_total():
    order = {"total": 100, "coupon": {"rate": 0.5, "max_discount": 15}}
    assert apply_coupon(order) == 85


@pytest.mark.parametrize("rate", [-0.1, 1.1, "bad"])
def test_invalid_rates_are_rejected(rate):
    with pytest.raises(ValueError):
        apply_coupon({"total": 100, "coupon": {"rate": rate}})


def test_subtotal_defaults_quantity_to_one_and_skips_void_items():
    items = [
        {"price": 10},
        {"price": 3, "qty": 2},
        {"price": 999, "qty": 1, "status": "void"},
    ]
    assert subtotal(items) == 16


def test_negative_quantity_is_rejected():
    with pytest.raises(ValueError):
        subtotal([{"price": 10, "qty": -1}])
