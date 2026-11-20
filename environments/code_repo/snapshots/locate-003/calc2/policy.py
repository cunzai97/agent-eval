def inside_window(observed_at, cutoff):
    """Apply the product's boundary policy to a normalized timestamp."""
    return observed_at > cutoff
