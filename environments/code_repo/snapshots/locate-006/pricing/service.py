from fx.rates import convert_usd
from .cache import cached_price
from .catalog import base_price
from .discounts import apply_tier


def unit_quote(sku, currency, tier="regular"):
    converted = cached_price(
        sku,
        currency,
        lambda: convert_usd(base_price(sku), currency),
    )
    return apply_tier(converted, tier)
