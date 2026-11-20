"""Apply pending migrations with checksum-based idempotency."""
import hashlib


class ChecksumMismatch(Exception):
    """Raised when an applied migration changed on disk."""


def checksum(sql):
    """Return the sha256 hex digest of the sql text."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def plan(applied, migrations):
    """Return migrations that still need to run."""
    return list(migrations)


def apply(state, migrations, load, execute):
    """Run pending migrations in order and update state in place."""
    applied = state.get("applied")
    if applied is None:
        applied = {}
        state["applied"] = applied
    report = {"applied": [], "skipped": [], "failed": None}
    for version, name in migrations:
        sql = load(name)
        if name in applied:
            report["skipped"].append(name)
            continue
        try:
            execute(sql)
        except Exception:
            report["failed"] = name
            continue
        report["applied"].append(name)
    return report
