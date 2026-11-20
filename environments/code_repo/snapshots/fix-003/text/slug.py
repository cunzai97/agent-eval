import re

from .normalize import normalize_text


def slugify(value, max_length=80):
    value = normalize_text(value).replace(" ", "-")
    value = re.sub(r"[^a-z0-9-]", "", value)
    return value[:max_length]
