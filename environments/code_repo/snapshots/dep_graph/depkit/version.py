"""Semantic version parsing, comparison and range matching."""
import re

_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
_OPERATORS = (">=", "<=", "==", "!=", ">", "<")


def parse(text):
    """Return (major, minor, patch, prerelease) for a version string."""
    if not isinstance(text, str):
        raise ValueError(f"invalid version: {text!r}")
    match = _PATTERN.match(text.strip())
    if not match:
        raise ValueError(f"invalid version: {text!r}")
    major, minor, patch, pre = match.groups()
    return int(major), int(minor), int(patch), pre


def _pre_key(pre):
    return pre or ""


def compare(left, right):
    """Return -1, 0 or 1 when left is lower, equal or higher than right."""
    a = parse(left)
    b = parse(right)
    for x, y in zip(a[:3], b[:3]):
        if x != y:
            return -1 if x < y else 1
    ka, kb = _pre_key(a[3]), _pre_key(b[3])
    if ka == kb:
        return 0
    return -1 if ka < kb else 1


def satisfies(text, spec):
    """Check a version string against a single operator spec such as '>=1.2.0'."""
    spec = spec.strip()
    for op in _OPERATORS:
        if spec.startswith(op):
            target = spec[len(op):].strip()
            result = compare(text, target)
            if op == ">=":
                return result > 0
            if op == "<=":
                return result < 0
            if op == "==":
                return result == 0
            if op == "!=":
                return result != 0
            if op == ">":
                return result > 0
            return result < 0
    raise ValueError(f"unsupported spec: {spec!r}")
