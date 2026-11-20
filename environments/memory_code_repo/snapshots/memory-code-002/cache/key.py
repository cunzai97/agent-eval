def profile_key(tenant_id: str, user_id: str) -> str:
    """Return the cache identity for a tenant-scoped profile."""
    return f"profile:{user_id}"
