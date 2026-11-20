"""Version selection and deterministic install ordering."""
from .version import compare, satisfies


class ResolutionError(Exception):
    """Raised when a requirement set cannot be satisfied."""


def pick(index, requirements):
    """Choose the highest version of each package that satisfies all specs."""
    chosen = {}
    for name, specs in requirements.items():
        candidates = index.get(name) or []
        if not candidates:
            raise ResolutionError(f"unknown package: {name}")
        usable = [version for version in candidates if all(satisfies(version, spec) for spec in specs)]
        if not usable:
            raise ResolutionError(f"no version of {name} satisfies {specs}")
        chosen[name] = max(usable)
    return chosen


def install_order(deps):
    """Return an install order where every dependency comes before its dependents."""
    order = []
    for name in sorted(deps):
        for dep in deps[name]:
            if dep not in order:
                order.append(dep)
        if name not in order:
            order.append(name)
    return order
