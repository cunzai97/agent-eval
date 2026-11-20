"""Prompt-literal reference implementation of script-006 audit.py (scratch, out of tree).

Rules asserted by tasks/script-dev/script-dev-006.yaml task_prompt:
  1. skip unparseable JSON / missing fields / illegal time / illegal status;
     status accepts a JSON int or a string of decimal digits only, and rejects booleans;
     ISO-8601 with Z and offsets, compared as UTC
  2. dedup by request_id keeping the latest ts FIRST, then keep ts >= since and status >= 400
  3. role join with unknown; path strips '?' and everything after it
  4. aggregate error_count / unique_users per role,path
  5. columns strictly role,path,error_count,unique_users; sort error_count desc, role asc, path asc
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DIGITS = set("0123456789")


def parse_status(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value and all(ch in DIGITS for ch in value):
        return int(value)
    return None


def parse_ts(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc)


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        record = {}
        for field in ("request_id", "user_id", "ts", "path", "status"):
            if field not in obj:
                break
            record[field] = obj[field]
        else:
            stamp = parse_ts(record["ts"])
            status = parse_status(record["status"])
            if stamp is None or status is None:
                continue
            if not isinstance(record["request_id"], str) or not isinstance(record["user_id"], str):
                continue
            if not isinstance(record["path"], str):
                continue
            record["_ts"] = stamp
            record["_status"] = status
            out.append(record)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--users", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    since = parse_ts(args.since)
    if since is None:
        print("illegal --since", file=sys.stderr)
        return 2

    roles: dict[str, str] = {}
    with Path(args.users).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("user_id"):
                roles[row["user_id"]] = (row.get("role") or "").strip() or "unknown"

    latest: dict[str, dict] = {}
    for record in load(Path(args.log)):
        keep = latest.get(record["request_id"])
        if keep is None or record["_ts"] > keep["_ts"]:
            latest[record["request_id"]] = record

    errors: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record in latest.values():
        if record["_ts"] < since or record["_status"] < 400:
            continue
        path = record["path"].split("?", 1)[0]
        role = roles.get(record["user_id"], "unknown")
        errors[(role, path)].append(record["user_id"])

    rows = sorted(
        ((role, path, len(users), len(set(users))) for (role, path), users in errors.items()),
        key=lambda item: (-item[2], item[0], item[1]),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["role", "path", "error_count", "unique_users"])
        for role, path, count, users in rows:
            writer.writerow([role, path, str(count), str(users)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
