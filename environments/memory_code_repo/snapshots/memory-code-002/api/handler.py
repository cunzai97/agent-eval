from profile.service import get_profile


def handle(tenant_id: str, user_id: str, repository):
    return get_profile(tenant_id, user_id, repository)
