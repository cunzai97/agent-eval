DATA = {
    ("tenant-a", "x"): {"name": "alpha", "enabled": True},
    ("tenant-b", "x"): {"name": "beta", "enabled": False},
}


def fetch(tenant, item_id):
    return dict(DATA[(tenant, item_id)])
