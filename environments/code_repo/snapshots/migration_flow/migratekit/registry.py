"""Migration file discovery and validation."""
import re

_NAME = re.compile(r"^(\d+)_([a-z0-9_]+)\.sql$")


def discover(files):
    """Return [(version, name)] for migration file names."""
    items = []
    for path in files:
        base = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        match = _NAME.match(base)
        if not match:
            raise ValueError(f"invalid migration name: {base}")
        items.append((int(match.group(1)), base))
    return items
