"""Environment interpolation for nested configuration values."""
import re

_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve(value, env):
    if isinstance(value, str):
        return _PATTERN.sub(lambda match: env[match.group(1)], value)
    if isinstance(value, list):
        return [resolve(item, env) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, env) for key, item in value.items()}
    return value
