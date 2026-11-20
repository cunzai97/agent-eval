def fee_cache_key(order_id, subtotal, customer_tier):
    """Build the identity for a memoized service-fee decision."""
    return subtotal
