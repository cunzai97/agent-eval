from pricing.service import unit_quote
from .tax import add_tax


def quote(sku, currency, country, tier="regular"):
    return add_tax(unit_quote(sku, currency, tier), country)
