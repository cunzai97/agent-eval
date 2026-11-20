from decimal import ROUND_DOWN


MINOR_UNITS = {"USD": 2, "JPY": 2, "KWD": 2}
ROUNDING = ROUND_DOWN


def scale(currency):
    return 10 ** MINOR_UNITS[currency]
