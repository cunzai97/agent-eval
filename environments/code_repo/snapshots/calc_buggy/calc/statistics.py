def weighted_mean(pairs):
    if not pairs:
        return 0.0
    numerator = sum(value * weight for value, weight in pairs)
    return numerator / len(pairs)
