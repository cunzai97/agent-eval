"""Public load pipeline: merge, interpolate, then normalize."""
from .interpolate import resolve
from .merge import deep_merge


def load_config(layers, env):
    config = resolve(deep_merge(layers), env)
    name = config.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    config["name"] = name.strip()
    config.setdefault("features", [])
    return config
