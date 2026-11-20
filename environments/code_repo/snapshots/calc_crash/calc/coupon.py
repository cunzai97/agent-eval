from .validation import coupon_rate


def apply_coupon(order):
    rate = coupon_rate(order.get("coupon"))
    discount = order["total"] * rate
    cap = order.get("coupon", {}).get("max_discount")
    if cap is not None:
        discount = max(discount, cap)
    return order["total"] - discount


def subtotal(items):
    return sum(item["price"] * item["qty"] for item in items)
