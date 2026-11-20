"""NEGATIVE CONTROL (visible-green): dedup and filters are right, but no field value is type-checked and a bad record may crash the run"""
import argparse, csv, json, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ("request_id", "user_id", "ts", "path", "status")

def parse_status(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return int(v)
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None

def parse_ts(v):
    if not isinstance(v, str):
        return None
    t = v.strip()
    if t.endswith("Z") or t.endswith("z"):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        return None
    return d.astimezone(timezone.utc)

def load(path):
    rows = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if not isinstance(obj, dict) or any(k not in obj for k in FIELDS):
            continue
        ts = parse_ts(obj["ts"])
        st = parse_status(obj["status"])
        if ts is None or st is None:
            continue
        rows.append({"id": obj["request_id"], "user": obj["user_id"], "ts": ts,
                     "path": obj["path"], "status": st})
    return rows

ap = argparse.ArgumentParser()
for f in ("--log", "--users", "--since", "--out"):
    ap.add_argument(f, required=True)
a = ap.parse_args()
since = parse_ts(a.since)
roles = {}
with Path(a.users).open(encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh):
        if row.get("user_id"):
            roles[row["user_id"]] = row.get("role", "")
records = load(a.log)
latest = {}
for r in records:
    if r["id"] not in latest or r["ts"] > latest[r["id"]]["ts"]:
        latest[r["id"]] = r
KEEP = [r for r in latest.values() if r["ts"] >= since and r["status"] >= 400]
groups = defaultdict(list)
for r in KEEP:
    groups[(roles.get(r["user"], "unknown"), r["path"].split("?", 1)[0])].append(r["user"])
out_rows = sorted(((role, path, len(us), len(set(us))) for (role, path), us in groups.items()),
                  key=lambda it: (-it[2], it[0], it[1]))
Path(a.out).parent.mkdir(parents=True, exist_ok=True)
with open(a.out, "w", encoding="utf-8", newline="") as fh:
    wr = csv.writer(fh, lineterminator="\n")
    wr.writerow(["role", "path", "error_count", "unique_users"])
    for row in out_rows:
        wr.writerow([row[0], row[1], str(row[2]), str(row[3])])
