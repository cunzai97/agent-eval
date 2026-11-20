from .base import subtotal
from .discounts import discounted
from .fees import service_fee
from .tax import tax


def order_final(order):
    amount = discounted(subtotal(order["lines"]), order.get("coupon"))
    amount += service_fee(order["id"], amount, order.get("tier", "regular"))
    return round(amount + tax(amount, order.get("country", "US")), 2)
