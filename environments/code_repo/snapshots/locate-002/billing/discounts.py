def discounted(total, coupon=None):
    return total * (1 - (coupon or 0))
