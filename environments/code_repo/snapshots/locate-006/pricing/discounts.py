def apply_tier(amount, tier):
    factor = {"regular": 1.0, "gold": 0.9}.get(tier, 1.0)
    return round(amount * factor, 2)
