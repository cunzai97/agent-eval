from billing.fees import clear
from billing.final import order_final


def setup_function():
    clear()


def order(order_id, tier="regular"):
    return {"id": order_id, "tier": tier, "lines": [{"price": 100, "qty": 1}]}


def test_regular_alone():
    assert order_final(order("a")) == 120


def test_vip_alone():
    assert order_final(order("b", "vip")) == 100


def test_same_subtotal_different_customer_does_not_reuse_fee():
    assert order_final(order("a")) == 120
    assert order_final(order("b", "vip")) == 100


def test_discount_path_is_not_the_problem():
    value = order("c")
    value["coupon"] = 0.1
    assert order_final(value) == 110
