from .cache import get
from .repository import fetch


def load_item(tenant, item_id):
    return get(tenant, item_id, lambda: fetch(tenant, item_id))
