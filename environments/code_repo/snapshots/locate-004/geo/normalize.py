def normalize_longitude(lon):
    """Fold a longitude into [-180, 180); 180 and -180 describe the same meridian."""
    return ((lon + 180) % 360) - 180
