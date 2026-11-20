"""Post-run hidden generalization checks for script-development snapshots.

The fixtures live outside the copied workspace, so an agent can inspect the
public examples but cannot tailor its implementation to the held-out rows.

Each snapshot registers N independent **cases**.  A case is one held-out input
scenario (shuffled/extra/ragged columns, empty files, tied instants, out of
order rows, unquoted CSV metacharacters, ...) that is executed in its own
throwaway workspace and scored on its own, so `run_hidden_checks` reports
`{"passed": n, "total": N}` instead of the single binary verdict it used to
return.  Difficulty is then tunable per task with plain assertions such as
`state.hidden_tests.total >= 5 and state.hidden_tests.passed == state.hidden_tests.total`.

Isolation is unchanged: every candidate script still runs inside
`docker run --rm --network none --read-only --memory 256m ... <SHELL_IMAGE>`，
镜像按 digest 钉死（见 `evalkit/envs/sandbox.py`），浮动标签会让 held-out 用例的解释器随日期漂移
against a bind-mounted throwaway copy of itself, never against the live work
dir, and every case gets a *fresh* temp dir, so case B can never read the
artifacts case A produced.

The `EXPECTED` table below is not hand-typed: it is derived by running the
reference implementations (kept out of tree) against every fixture, so every
held-out number is a measured result rather than a guess.  Case ids are global
across snapshots, which keeps `FIXTURES` / `EXPECTED` flat and addressable.
"""
from __future__ import annotations
from .sandbox import SHELL_IMAGE, docker_run

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Callable


# --------------------------------------------------------------------------
# sandbox plumbing (semantics unchanged from the original single-case version)
# --------------------------------------------------------------------------
def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def _run(args: list[str], cwd: Path) -> tuple[bool, str]:
    target = Path(args[1])
    try:
        # nested entry points (script-008's pipeline/main.py) must stay addressable
        script = str(target.relative_to(cwd))
    except ValueError:
        script = target.name
    command = [
        "--rm", "--network", "none", "--read-only",
                    "-e", "PYTHONDONTWRITEBYTECODE=1",
        "--pids-limit", "64", "--memory", "256m", "--cpus", "1",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--mount", f"type=bind,source={cwd},target=/workspace",
        "--workdir", "/workspace", SHELL_IMAGE, "python", script, *args[2:],
    ]
    try:
        proc = docker_run(command, timeout=30, cwd=cwd)
    except subprocess.TimeoutExpired:
        return False, "hidden command timed out"
    detail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-1200:]
    return proc.returncode == 0, detail


def _copy_script(work_dir: Path, temp_dir: Path, name: str) -> Path:
    source = work_dir / name
    if not source.is_file():
        raise AssertionError(f"missing script: {name}")
    target = temp_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def _copy_tree(work_dir: Path, temp_dir: Path, rel: str) -> None:
    """Copy a subtree of the candidate workspace, dropping stale artefacts."""
    source = work_dir / rel
    if not source.is_dir():
        raise AssertionError(f"missing directory: {rel}")
    shutil.copytree(
        source, temp_dir / rel,
        ignore=shutil.ignore_patterns("out", "__pycache__", "*.pyc", ".pytest_cache"),
    )


def _expect(path: Path, expected: list[list[str]]) -> None:
    actual = _rows(path) if path.is_file() else []
    if actual != expected:
        raise AssertionError(f"{path.name}: expected={expected!r}, actual={actual!r}")


# --------------------------------------------------------------------------
# held-out fixtures, grouped per case id
# --------------------------------------------------------------------------
S003_HEADER = "record_id,updated_at,status,value\n"

FIXTURES: dict[str, dict] = {
    # ---------------- script-002 / freq.py ----------------
    "unicode-fold-and-stopwords": dict(
        script="freq.py",
        files={
            "input.txt": "AI ai β2 β2 １２ １２ café cafe\u0301 Straße STRASSE x 7\n",
            "stop.txt": "strasse\n",
        },
        runs=[["--input", "input.txt", "--stopwords", "stop.txt", "--out", "held/freq.csv"]],
        outs=["held/freq.csv"],
    ),
    # every token is a stopword or shorter than two characters -> header only;
    # a second input whose counts tie must break on word ascending
    "count-ties-and-all-stopwords": dict(
        script="freq.py",
        files={
            "empty.txt": "a b 1 stop stop STOP 日本 ..!!\n",
            "ties.txt": "aa bb AA BB 日本 日本 stop\n",
            "stop.txt": "stop\n日本\n",
        },
        runs=[
            ["--input", "empty.txt", "--stopwords", "stop.txt", "--out", "held/empty.csv"],
            ["--input", "ties.txt", "--stopwords", "stop.txt", "--out", "held/ties.csv"],
        ],
        outs=["held/empty.csv", "held/ties.csv"],
    ),

    # ---------------- script-003 / summarize.py ----------------
    "dedup-status-and-timezone": dict(
        script="summarize.py",
        files={
            "a.csv": S003_HEADER + "r1,2025-01-01T00:00:00Z,ok,0.10\n"
                    "r1,2025-01-01T00:01:00Z,ok,0.20\n"
                    "r2,2025-01-01T00:00:00Z,ok,99\n"
                    "r2,2025-01-01T00:02:00Z,cancelled,99\n"
                    "r3,2025-01-01T00:00:00Z,ok,-1\n",
            "b.csv": S003_HEADER + "x1,2025-01-01T00:00:00+00:00,ok,1.11\n"
                    "x2,2025-01-01T08:00:00+08:00,ok,2.22\n"
                    "x3,2025-01-01T00:00:00Z,ok,bad\n",
            "c.csv": S003_HEADER + "z1,2025-01-01T00:00:00Z,ok,10.00\n",
        },
        outs=["out/report.csv"],
    ),
    "utc-instant-not-string-order": dict(
        script="summarize.py",
        files={
            "a.csv": S003_HEADER + "k1,2025-01-01T09:00:00+08:00,ok,5\n"
                    "k1,2025-01-01T02:00:00Z,ok,7\n"
                    "k2,2025-01-02T00:00:00Z,ok,3\n",
            "b.csv": S003_HEADER + "k3,2025-01-01T00:00:00Z,ok,1.50\n"
                    "k3,2024-12-31T20:00:00-05:00,ok,8\n",
            "c.csv": S003_HEADER + "k4,2025-01-01T00:00:00Z,ok,2\n",
        },
        outs=["out/report.csv"],
    ),
    "tied-instants-and-zero-values": dict(
        script="summarize.py",
        files={
            "a.csv": S003_HEADER + "t1,2025-01-01T00:00:00Z,ok,1\n"
                    "t1,2024-12-31T19:00:00-05:00,ok,2\n"
                    "t2,2025-01-01T00:00:00Z,skip,99\n"
                    "t2,2025-01-01T00:00:00Z,ok,4\n",
            "b.csv": S003_HEADER + "t3,2025-01-01T12:00:00+00:00,ok,10\n"
                    "t3,2025-01-01T20:00:00+08:00,ok,20\n",
            "c.csv": S003_HEADER + "t4,2025-01-01T00:00:00Z,ok,0\n",
        },
        outs=["out/report.csv"],
    ),
    "shuffled-extra-and-ragged-columns": dict(
        script="summarize.py",
        files={
            "a.csv": "value,record_id,status,updated_at,note\n"
                     "3,p1,ok,2025-01-03T00:00:00Z,older-but-valid\n"
                     "100,p1,bad,2025-01-05T00:00:00Z,newest-is-invalid\n",
            "b.csv": "updated_at,record_id,value,status\n"
                     "2025-01-01T00:00:00Z,q1,2.50,ok\n"
                     "2025-01-02T00:00:00Z,q2,-5,ok,extra-field\n"
                     "2025-01-03T00:00:00Z,q3,4\n",
            "c.csv": S003_HEADER,
        },
        outs=["out/report.csv"],
    ),
    "unsorted-rows-many-versions-per-key": dict(
        script="summarize.py",
        files={
            "a.csv": S003_HEADER + "s3,2025-01-04T00:00:00Z,ok,4\n"
                    "s1,2025-01-01T00:00:00Z,ok,1\n"
                    "s2,2025-01-05T00:00:00Z,skip,99\n"
                    "s1,2025-01-09T00:00:00Z,ok,9\n"
                    "s3,2025-01-02T00:00:00Z,ok,2\n"
                    "s2,2025-01-03T00:00:00Z,ok,3\n"
                    "s1,2025-01-06T00:00:00Z,ok,6\n",
            "b.csv": S003_HEADER + "u1,2025-01-01T00:00:00Z,ok,0.10\n"
                    "u1,2025-01-01T00:00:01Z,ok,0.20\n"
                    "u2,2025-01-01T00:00:00Z,ok,1000000\n",
            "c.csv": S003_HEADER + "v1,2025-01-01T00:00:00Z,ok,0.05\n"
                    "v2,2025-01-01T00:00:00Z,ok,0.05\n",
        },
        outs=["out/report.csv"],
    ),
    "naive-timestamps-and-empty-file": dict(
        script="summarize.py",
        files={
            "a.csv": S003_HEADER + "n1,2025-01-01 00:00:00,ok,10\n"
                    "n1,2025-01-02T00:00:00Z,ok,1\n"
                    "n2,not-a-time,ok,50\n"
                    "n3,2025-01-03T00:00:00Z,ok,2.50\n",
            "b.csv": "",
            "c.csv": S003_HEADER + "z1,2025-01-01T00:00:00Z,ok,50\n"
                    "z1,2025-01-02T00:00:00Z,ok,bad\n"
                    "z2,2025-01-01T00:00:00Z,,7\n",
        },
        outs=["out/report.csv"],
    ),

    # ---------------- script-004 / script.py ----------------
    "report-and-compare-basic": dict(
        script="script.py",
        files={"held.csv": "group,value\na,0.1\na,0.2\nb,1\nb,2\nb,3\nc,4\nc,-1\nc,bad\n"},
        runs=[
            ["report", "--file", "held.csv", "--out", "held/report.csv"],
            ["compare", "--file", "held.csv", "--left", "a", "--right", "b",
             "--out", "held/compare.csv"],
        ],
        outs=["held/report.csv", "held/compare.csv"],
    ),
    "zero-values-and-all-invalid-group": dict(
        script="script.py",
        files={"zeros.csv": "group,value\nz,0\nz,0\ne,-1\ne,bad\no,0.5\n"},
        runs=[
            ["report", "--file", "zeros.csv", "--out", "held/report.csv"],
            ["compare", "--file", "zeros.csv", "--left", "z", "--right", "o",
             "--out", "held/compare.csv"],
        ],
        outs=["held/report.csv", "held/compare.csv"],
    ),
    "quoted-group-names-and-column-order": dict(
        script="script.py",
        files={"quoted.csv": "value,group,note\n"
                             '1,"a,one",x\n2,"a,one",y\n3,"say ""hi""",z\n4,"",w\n'},
        runs=[
            ["report", "--file", "quoted.csv", "--out", "held/report.csv"],
            ["compare", "--file", "quoted.csv", "--left", "", "--right", "a,one",
             "--out", "held/compare.csv"],
        ],
        outs=["held/report.csv", "held/compare.csv"],
    ),
    "missing-group-on-one-side": dict(
        script="script.py",
        files={"nums2.csv": "group,value\np,7\np,3\n"},
        runs=[
            ["compare", "--file", "nums2.csv", "--left", "p", "--right", "ghost",
             "--out", "held/compare.csv"],
            ["compare", "--file", "nums2.csv", "--left", "ghost", "--right", "p",
             "--out", "held/compare2.csv"],
        ],
        outs=["held/compare.csv", "held/compare2.csv"],
    ),
    "lexicographic-group-order": dict(
        script="script.py",
        files={"natural.csv": "group,value\ng10,12345678.90\ng2,0\ng10,5\ng1,1\n"},
        runs=[["report", "--file", "natural.csv", "--out", "held/report.csv"]],
        outs=["held/report.csv"],
    ),
    "header-only-input": dict(
        script="script.py",
        files={"bare.csv": "group,value\n"},
        runs=[
            ["report", "--file", "bare.csv", "--out", "held/report.csv"],
            ["compare", "--file", "bare.csv", "--left", "x", "--right", "y",
             "--out", "held/compare.csv"],
        ],
        outs=["held/report.csv", "held/compare.csv"],
    ),

    # ---------------- script-005 / logs.py ----------------
    "dedup-p95-basic": dict(
        script="logs.py",
        files={
            "app.log": "\n".join(
                ["2025-01-01T00:00:00Z INFO r1 10 old"]
                + [f"2025-01-01T01:{i:02d}:00Z ERROR e{i} {i} sample" for i in range(1, 21)]
                + ["bad line", "2025-01-01T00:00:00Z DEBUG bad 9 nope"]
            ) + "\n",
            "app2.log": "2025-01-01T08:30:00+08:00 ERROR r1 40 newest\n"
                        "2025-01-01T01:00:00Z WARN r2 100 old\n"
                        "2025-01-01T01:01:00Z WARN r2 200 newest\n"
                        "2025-01-02T00:15:00+08:00 INFO r3 30 offset\n",
        },
        outs=["out/levels.csv"],
    ),
    "p95-nearest-rank-not-max": dict(
        script="logs.py",
        files={
            "app.log": "".join(
                f"2025-01-01T12:{i:02d}:00Z ERROR q{i:02d} {10 * i} api\n" for i in range(1, 21)
            ),
            "app2.log": "2025-01-01T13:00:00Z WARN w1 5 slow\n"
                        "2025-01-01T13:01:00Z WARN w2 5 slow\n"
                        "2025-01-01T13:02:00Z WARN w3 9 slow\n"
                        "2025-01-01T13:03:00Z INFO i1 7 ok\n",
        },
        outs=["out/levels.csv"],
    ),
    "cross-file-dedup-changes-level": dict(
        script="logs.py",
        files={
            "app.log": "2025-01-03T10:00:00Z WARN r2 50 first\n"
                       "2025-01-03T09:00:00Z ERROR r1 100 stale-error\n",
            "app2.log": "2025-01-03T11:00:00Z INFO r1 80 recovered\n"
                        "2025-01-03T08:00:00Z WARN r2 60 older-must-lose\n",
        },
        outs=["out/levels.csv"],
    ),
    "utc-date-not-first-ten-chars": dict(
        script="logs.py",
        files={
            "app.log": "2025-01-02T01:30:00+08:00 ERROR a1 40 local-day-trap\n"
                       "2025-01-01T23:30:00-05:00 ERROR a2 70 local-day-trap\n"
                       "2025-01-01T05:00:00Z INFO a3 10 plain\n",
            "app2.log": "2025-01-01T04:00:00Z WARN a4 20 out-of-order-earlier\n"
                        "2025-01-02T09:00:00+09:00 ERROR a5 90 utc-2025-01-02\n",
        },
        outs=["out/levels.csv"],
    ),
    "malformed-line-shapes": dict(
        script="logs.py",
        files={
            "app.log": "\n".join([
                "",
                "   ",
                "junk",
                "2025-01-04T10:00:00Z info r1 10 lowercase-level",
                "2025-01-04T10:01:00Z FATAL r2 10 unknown-level",
                "2025-01-04T10:02:00Z ERROR r3 -5 negative-latency",
                "2025-01-04T10:03:00Z ERROR r4 abc non-numeric-latency",
                "2025-01-04T10:04:00 ERROR r5 10 naive-timestamp",
                "2025-01-04T10:05:00Z\tERROR\tr6\t12\ttab-separated",
                "2025-01-04T10:06:00Z ERROR r7 30 two words message here",
                "2025-01-04T10:07:00Z WARN r8",
            ]) + "\n",
            "app2.log": "2025-01-04T12:00:00Z ERROR r9 77 ok line\n",
        },
        outs=["out/levels.csv"],
    ),
    "no-valid-lines-at-all": dict(
        script="logs.py",
        files={
            "app.log": "2025-01-05T10:00:00Z TRACE r1 10 nope\n"
                       "not a log line at all\n"
                       "2025-01-05T10:00:00Z ERROR r2 bad latency\n",
            "app2.log": "",
        },
        outs=["out/levels.csv"],
    ),
    # two records whose timestamps denote the *same* instant, spelled so that a
    # string comparison points at the wrong one; the later row must win - once
    # across files (where it also flips the level) and once inside one file
    "tied-instants-across-and-within-files": dict(
        script="logs.py",
        files={
            "app.log": "2025-01-06T17:00:00+08:00 ERROR t1 100 first-row-looks-later\n"
                       "2025-01-06T18:30:00+08:30 WARN t2 40 within-file-first\n"
                       "2025-01-06T10:00:00Z WARN t2 90 within-file-later\n",
            "app2.log": "2025-01-06T09:00:00Z INFO t1 20 later-row-is-the-tie\n",
        },
        outs=["out/levels.csv"],
    ),

    # ---------------- script-006 / audit.py ----------------
    "dedup-since-role-join": dict(
        script="audit.py",
        files={
            "users.csv": "user_id,role\nu1,admin\nu2,member\n",
            "held.jsonl": "\n".join(json.dumps(item) for item in [
                {"request_id": "a", "user_id": "u1", "ts": "2025-01-01T00:00:00Z",
                 "path": "/old", "status": 500},
                {"request_id": "a", "user_id": "u1", "ts": "2025-01-01T00:01:00Z",
                 "path": "/old", "status": 200},
                {"request_id": "b", "user_id": "u2", "ts": "2025-01-01T08:00:00+08:00",
                 "path": "/x?q=1", "status": "404"},
                {"request_id": "c", "user_id": "u1", "ts": "2025-01-01T00:02:00Z",
                 "path": "/bool", "status": True},
                {"request_id": "d", "user_id": "u1", "ts": "2025-01-01T00:03:00Z",
                 "path": "/space", "status": " 500"},
                {"request_id": "e", "user_id": "missing", "ts": "2025-01-01T00:04:00Z",
                 "path": "/z?a=1", "status": 400},
                {"request_id": "f", "user_id": "u1", "ts": "2024-12-31T23:59:59Z",
                 "path": "/before", "status": 500},
                {"request_id": "g", "user_id": "u1", "ts": "2025-01-01T00:05:00Z",
                 "path": "/x?admin=1", "status": 503},
            ]) + "\nnot-json\n",
        },
        runs=[["--log", "held.jsonl", "--users", "users.csv",
               "--since", "2025-01-01T00:00:00Z", "--out", "held/audit.csv"]],
        outs=["held/audit.csv"],
    ),
    "empty-and-no-matching-records": dict(
        script="audit.py",
        files={
            "users.csv": "user_id,role\nu1,admin\n",
            "held.jsonl": "\n".join([
                "not json",
                "",
                json.dumps({"request_id": "a", "user_id": "u1", "path": "/x", "status": 500}),
                json.dumps({"request_id": "b", "user_id": "u1",
                            "ts": "2024-01-01T00:00:00Z", "path": "/x", "status": 500}),
                json.dumps({"request_id": "c", "user_id": "u1",
                            "ts": "2025-02-01T00:00:00Z", "path": "/y", "status": 399}),
            ]) + "\n",
        },
        runs=[["--log", "held.jsonl", "--users", "users.csv",
               "--since", "2025-01-01T00:00:00Z", "--out", "held/audit.csv"]],
        outs=["held/audit.csv"],
    ),
    "unique-users-and-count-ties": dict(
        script="audit.py",
        files={
            "users.csv": "user_id,role\nu1,admin\nu2,member\nu3,member\nu4,ops\n",
            "held.jsonl": "\n".join(json.dumps(item) for item in [
                {"request_id": "r1", "user_id": "u1", "ts": "2025-03-01T00:00:00Z",
                 "path": "/api", "status": 500},
                {"request_id": "r2", "user_id": "u2", "ts": "2025-03-01T00:01:00Z",
                 "path": "/api?a=1", "status": 502},
                {"request_id": "r3", "user_id": "u3", "ts": "2025-03-01T00:02:00Z",
                 "path": "/api", "status": 503},
                {"request_id": "r4", "user_id": "u2", "ts": "2025-03-01T00:03:00Z",
                 "path": "/api", "status": 404},
                {"request_id": "r5", "user_id": "u4", "ts": "2025-03-01T09:00:00+09:00",
                 "path": "/health?deep=1", "status": 500},
                {"request_id": "r6", "user_id": "unknown-guy", "ts": "2025-03-01T00:05:00Z",
                 "path": "/health", "status": 401},
                {"request_id": "r7", "user_id": "u1", "ts": "2025-03-01T00:06:00Z",
                 "path": "/health", "status": 403},
                {"request_id": "r8", "user_id": "u1", "ts": "2025-03-01T00:07:00Z",
                 "path": "/exact-boundary", "status": "500"},
            ]) + "\n",
        },
        runs=[["--log", "held.jsonl", "--users", "users.csv",
               "--since", "2025-03-01T00:00:00Z", "--out", "held/audit.csv"]],
        outs=["held/audit.csv"],
    ),

    # ---------------- merge_csv / merge_csv.py ----------------
    "held-outer-join": dict(
        script="merge_csv.py",
        files={
            "left-held.csv": "employee_id,updated_at,name,email\n"
                             "m1,2025-01-01T00:00:00Z,Old,old@test\n"
                             "m1,2025-01-01T00:01:00Z,New,left@test\n"
                             "m2,2025-01-01T00:00:00Z,Left Only,l@test\n",
            "right-held.csv": "employee_id,updated_at,email,city\n"
                              "m1,2025-01-01T00:00:00Z,,Old City\n"
                              "m1,2025-01-01T00:02:00Z,right@test,New City\n"
                              "m3,2025-01-01T00:00:00Z,,Right Only\n",
        },
        runs=[["--left", "left-held.csv", "--right", "right-held.csv",
               "--out", "held/merged.csv"]],
        outs=["held/merged.csv"],
    ),
    "merge-utc-instant-not-string-order": dict(
        script="merge_csv.py",
        files={
            "l.csv": "employee_id,updated_at,name,email\n"
                     "z1,2025-06-01T09:00:00+08:00,Wrong Time,W@old\n"
                     "z1,2025-06-01T02:00:00Z,Right Time,w@new\n",
            "r.csv": "employee_id,updated_at,email,city\n"
                     "z1,2025-06-01T00:00:00Z,,Base City\n"
                     "z1,2025-06-01T03:00:00Z,z@r.test,Latest City\n",
        },
        runs=[["--left", "l.csv", "--right", "r.csv", "--out", "held/merged.csv"]],
        outs=["held/merged.csv"],
    ),
    "one-side-header-only": dict(
        script="merge_csv.py",
        files={
            "l.csv": "employee_id,updated_at,name,email\n",
            "r.csv": "employee_id,updated_at,email,city\n"
                     "c1,2025-01-01T00:00:00Z,c@r.test,City C\n",
            "l2.csv": "employee_id,updated_at,name,email\n"
                      "c2,2025-01-01T00:00:00Z,Only Left,c2@l.test\n",
            "r2.csv": "employee_id,updated_at,email,city\n",
        },
        runs=[
            ["--left", "l.csv", "--right", "r.csv", "--out", "held/merged.csv"],
            ["--left", "l2.csv", "--right", "r2.csv", "--out", "held/merged2.csv"],
        ],
        outs=["held/merged.csv", "held/merged2.csv"],
    ),
    "many-unsorted-versions-per-key": dict(
        script="merge_csv.py",
        files={
            "l.csv": "employee_id,updated_at,name,email\n"
                     "q9,2025-01-05T00:00:00Z,Q Nine Latest,q9@l\n"
                     "q7,2025-01-01T00:00:00Z,Q Seven Old,q7@old\n"
                     "q9,2025-01-02T00:00:00Z,Q Nine Old,\n"
                     "q7,2025-01-03T00:00:00Z,Q Seven New,q7@new\n"
                     "q9,2025-01-04T00:00:00Z,Q Nine Middle,\n",
            "r.csv": "employee_id,updated_at,email,city\n"
                     "q7,2025-01-02T00:00:00Z,,Old Town\n"
                     "q8,2025-01-02T00:00:00Z,q8@r.test,Only Right\n"
                     "q7,2025-01-09T00:00:00Z,q7@r.test,New Town\n",
        },
        runs=[["--left", "l.csv", "--right", "r.csv", "--out", "held/merged.csv"]],
        outs=["held/merged.csv"],
    ),
    "email-fallback-and-blanks": dict(
        script="merge_csv.py",
        files={
            "l.csv": "employee_id,updated_at,name,email\n"
                     "e1,2025-01-01T00:00:00Z,E One Left,e1@l.test\n"
                     "e2,2025-01-01T00:00:00Z,,\n"
                     "e3,2025-01-01T00:00:00Z,E Three,three@l.test\n",
            "r.csv": "employee_id,updated_at,email,city\n"
                     "e1,2025-01-02T00:00:00Z,,E One City\n"
                     "e2,2025-01-02T00:00:00Z,e2@r.test,\n"
                     "e3,2025-01-02T00:00:00Z,,Blank City\n",
        },
        runs=[["--left", "l.csv", "--right", "r.csv", "--out", "held/merged.csv"]],
        outs=["held/merged.csv"],
    ),
    "shuffled-columns-and-quotes": dict(
        script="merge_csv.py",
        files={
            "l.csv": "note,email,updated_at,employee_id,name\n"
                     "n1,\"a,b@x.test\",2025-01-01T00:00:00Z,k2,\"Doe, Jane\"\n"
                     "n2,plain,2025-01-01T00:00:00Z,k1,\"He said \"\"hi\"\"\"\n",
            "r.csv": "city,employee_id,updated_at,email\n"
                     "San Francisco,10,2025-01-01T00:00:00Z,ten@r\n"
                     "\"Rome, IT\",k2,2025-01-02T00:00:00Z,\n"
                     "Oslo,k1,2024-12-30T00:00:00Z,k1@r\n",
        },
        runs=[["--left", "l.csv", "--right", "r.csv", "--out", "held/merged.csv"]],
        outs=["held/merged.csv"],
    ),
    # both sides carry an exact timestamp tie spelled two different ways; the row
    # that appears later wins, and the winning right-hand row then has no email
    "tied-instants-keep-later-row": dict(
        script="merge_csv.py",
        files={
            "l.csv": "employee_id,updated_at,name,email\n"
                     "w1,2025-06-01T20:00:00+08:00,First Spell,f@l.test\n"
                     "w1,2025-06-01T12:00:00Z,Later Spell,l@l.test\n"
                     "w2,2025-06-01T12:00:00Z,Plain Left,\n",
            "r.csv": "employee_id,updated_at,email,city\n"
                     "w1,2025-06-01T13:00:00Z,w@r.test,Real City\n"
                     "w1,2025-06-01T21:00:00+08:00,,Older Looking City\n"
                     "w2,2025-06-01T12:30:00Z,,Later Town\n",
        },
        runs=[["--left", "l.csv", "--right", "r.csv", "--out", "held/merged.csv"]],
        outs=["held/merged.csv"],
    ),
}


# --------------------------------------------------------------------------
# script-007 / report.py  (policy vs style-matched control)
# --------------------------------------------------------------------------
S007_HEADER = "date,code,board,ret_pct\n"


def _s007_rows(
    dates: list[str],
    panel: dict[str, tuple[str, list[str]]],
    scramble: bool = False,
) -> str:
    """Render a date x code return panel as the panel CSV the CLI is fed.

    `scramble` swaps the two halves of the body so the held-out file is neither
    date-ordered nor code-ordered, which punishes implementations that assume
    the rows arrive sorted.
    """
    body = []
    for index, day in enumerate(dates):
        for code, (board, values) in panel.items():
            body.append(f"{day},{code},{board},{values[index]}")
    if scramble:
        half = len(body) // 2
        body = body[half:] + body[:half]
    return "\n".join([S007_HEADER, *body]) + "\n"


S007_HELD_DATES = [
    "2025-05-05", "2025-05-06", "2025-05-07", "2025-05-08",
    "2025-05-09", "2025-05-12", "2025-05-13",
]
S007_HELD_PANEL = {
    # six names -> an even panel, so the median is the mean of the two middle rows
    "KC1": ("K", ["3.00", "3.00", "3.00", "5.00", "5.00", "1.00", "1.00"]),
    "KC2": ("K", ["1.00", "1.00", "1.00", "1.00", "1.00", "1.00", "1.00"]),
    "SH1": ("H", ["-1.00", "0.50", "-0.50", "-1.00", "-1.00", "-1.00", "0.00"]),
    "SH2": ("H", ["0.00", "1.00", "-1.00", "-1.00", "-2.00", "0.00", "-1.00"]),
    "SZ1": ("Z", ["1.00", "-1.00", "0.50", "-3.00", "-2.00", "-2.00", "-1.00"]),
    "SG1": ("G", ["-2.00", "1.00", "0.00", "-4.00", "-3.00", "-2.00", "-1.00"]),
}
S007_TIE_DATES = [
    "2025-07-01", "2025-07-02", "2025-07-03", "2025-07-04", "2025-07-07",
]
S007_TIE_PANEL = {
    # A and B have byte-identical histories on the trailing window -> exact tie.
    "KBA": ("K", ["1.00", "1.00", "1.00", "-5.00", "9.00"]),
    "KBB": ("K", ["1.00", "1.00", "1.00", "6.00", "-2.00"]),
    "KMC": ("K", ["-1.00", "-1.00", "2.00", "1.00", "1.00"]),
    "HD1": ("H", ["-2.00", "3.00", "-1.00", "-1.00", "2.00"]),
    "HD2": ("H", ["1.00", "-1.00", "-1.00", "1.00", "-1.00"]),
}
S007_TINY_DATES = ["2025-09-01", "2025-09-02", "2025-09-03", "2025-09-04"]
S007_TINY_PANEL = {
    "KA": ("K", ["1.00", "1.00", "1.00", "2.00"]),
    "KB": ("K", ["0.50", "0.50", "0.50", "-1.00"]),
    "HC": ("H", ["-0.50", "1.50", "0.00", "1.00"]),
}
S007_CRASH_DATES = [
    "2025-11-03", "2025-11-04", "2025-11-05", "2025-11-06", "2025-11-07", "2025-11-10",
]
S007_CRASH_PANEL = {
    "KP1": ("K", ["5.00", "5.00", "5.00", "-20.00", "1.00", "2.00"]),
    "KP2": ("K", ["1.00", "1.00", "1.00", "1.00", "1.00", "1.00"]),
    "KP3": ("K", ["0.00", "0.00", "2.00", "0.00", "-1.00", "-1.60"]),
    "HX1": ("H", ["-1.00", "-1.00", "-1.00", "-1.00", "-1.00", "-1.00"]),
    "HX2": ("H", ["-2.00", "1.00", "-1.00", "0.00", "-1.50", "-1.00"]),
    "HX3": ("H", ["3.00", "-4.00", "1.00", "1.00", "-1.00", "0.00"]),
    "HX4": ("H", ["0.50", "0.50", "0.50", "0.50", "0.50", "-9.00"]),
}

FIXTURES["held-panel-selection-branch"] = dict(
    script="report.py",
    files={"held_returns.csv": _s007_rows(S007_HELD_DATES, S007_HELD_PANEL)},
    runs=[["--data", "held_returns.csv", "--out", "held/report.csv"]],
    outs=["held/report.csv"],
)
FIXTURES["momentum-tie-on-identical-history"] = dict(
    script="report.py",
    files={"held_returns.csv": _s007_rows(S007_TIE_DATES, S007_TIE_PANEL)},
    runs=[["--data", "held_returns.csv", "--out", "held/report.csv"]],
    outs=["held/report.csv"],
)
FIXTURES["tiny-panel-single-active-day"] = dict(
    script="report.py",
    files={"held_returns.csv": _s007_rows(S007_TINY_DATES, S007_TINY_PANEL)},
    runs=[["--data", "held_returns.csv", "--out", "held/report.csv"]],
    outs=["held/report.csv"],
)
FIXTURES["leader-crashes-after-entry"] = dict(
    script="report.py",
    files={"held_returns.csv": _s007_rows(S007_CRASH_DATES, S007_CRASH_PANEL, scramble=True)},
    runs=[["--data", "held_returns.csv", "--out", "held/report.csv"]],
    outs=["held/report.csv"],
)


# --------------------------------------------------------------------------
# script-008 / pipeline/main.py  (cwd-independent paths)
# --------------------------------------------------------------------------
S008_IN_HEADER = "date,device,region,kwh\n"
S008_HELD = (
    "2025-04-01,d1,north,10\n2025-04-01,d2,south,4\n2025-04-01,d3,north,2.5\n"
    "2025-04-02,d1,north,12\n2025-04-02,d2,south,6\n2025-04-02,d4,west,7\n"
    "2025-04-03,d3,north,1.5\n2025-04-03,d4,west,3\n2025-04-03,d5,south,0\n"
    "2025-04-04,d5,south,9.25\n2025-04-04,d1,north,-1\n2025-04-04,d2,south,bad\n"
)

FIXTURES["nested-entry-point"] = dict(
    script="pipeline/main.py", tree="pipeline",
    files={"pipeline/data/in.csv": S008_IN_HEADER + S008_HELD},
    runs=[[]],
    outs=["pipeline/out/report.csv"],
)
FIXTURES["blanks-negatives-and-ties"] = dict(
    script="pipeline/main.py", tree="pipeline",
    files={"pipeline/data/in.csv": S008_IN_HEADER + (
        "2025-04-01,d7,west,5\n2025-04-01,d8,west,5\n2025-04-01,d9,east,0\n"
        "2025-04-02,d7,west,5\n2025-04-02,d9,east,2.5\n2025-04-02,d10,east,7.5\n"
        "2025-04-03,d8,west,-3\n2025-04-03,d10,east,1\n2025-04-03,d11,south,\n"
    )},
    runs=[[]],
    outs=["pipeline/out/report.csv"],
)
FIXTURES["single-device-single-day"] = dict(
    script="pipeline/main.py", tree="pipeline",
    files={"pipeline/data/in.csv": S008_IN_HEADER + "2025-04-01,d1,north,4\n"},
    runs=[[]],
    outs=["pipeline/out/report.csv"],
)


# --------------------------------------------------------------------------
# expected outputs -- BEGIN GENERATED (values measured from the reference runs)
# --------------------------------------------------------------------------
EXPECTED: dict[str, dict[str, list[list[str]]]] = {
    # ---- script-002 (2 held-out cases) ----
    "unicode-fold-and-stopwords": {"held/freq.csv": [
        ["word", "count"], ["ai", "2"], ["β2", "2"], ["１２", "2"], ["cafe", "1"], ["café", "1"]]},
    "count-ties-and-all-stopwords": {
        "held/empty.csv": [["word", "count"]],
        "held/ties.csv": [["word", "count"], ["aa", "2"], ["bb", "2"]],
    },
    # ---- script-003 (6 held-out cases) ----
    "dedup-status-and-timezone": {"out/report.csv": [
        ["source", "valid_count", "total", "avg"], ["a.csv", "1", "0.20", "0.20"],
        ["b.csv", "2", "3.33", "1.67"], ["c.csv", "1", "10.00", "10.00"],
        ["grand", "4", "13.53", "3.38"]]},
    "utc-instant-not-string-order": {"out/report.csv": [
        ["source", "valid_count", "total", "avg"], ["a.csv", "2", "10.00", "5.00"],
        ["b.csv", "1", "8.00", "8.00"], ["c.csv", "1", "2.00", "2.00"],
        ["grand", "4", "20.00", "5.00"]]},
    "tied-instants-and-zero-values": {"out/report.csv": [
        ["source", "valid_count", "total", "avg"], ["a.csv", "2", "6.00", "3.00"],
        ["b.csv", "1", "20.00", "20.00"], ["c.csv", "1", "0.00", "0.00"],
        ["grand", "4", "26.00", "6.50"]]},
    "shuffled-extra-and-ragged-columns": {"out/report.csv": [
        ["source", "valid_count", "total", "avg"], ["a.csv", "0", "0.00", "0.00"],
        ["b.csv", "1", "2.50", "2.50"], ["c.csv", "0", "0.00", "0.00"],
        ["grand", "1", "2.50", "2.50"]]},
    "unsorted-rows-many-versions-per-key": {"out/report.csv": [
        ["source", "valid_count", "total", "avg"], ["a.csv", "2", "13.00", "6.50"],
        ["b.csv", "2", "1000000.20", "500000.10"], ["c.csv", "2", "0.10", "0.05"],
        ["grand", "6", "1000013.30", "166668.88"]]},
    "naive-timestamps-and-empty-file": {"out/report.csv": [
        ["source", "valid_count", "total", "avg"], ["a.csv", "2", "3.50", "1.75"],
        ["b.csv", "0", "0.00", "0.00"], ["c.csv", "0", "0.00", "0.00"],
        ["grand", "2", "3.50", "1.75"]]},
    # ---- script-004 (6 held-out cases) ----
    "report-and-compare-basic": {
        "held/compare.csv": [["metric", "left", "right", "delta"], ["count", "2", "3", "1"],
                             ["sum", "0.30", "6.00", "5.70"], ["avg", "0.15", "2.00", "1.85"]],
        "held/report.csv": [["group", "count", "sum", "avg"], ["a", "2", "0.30", "0.15"],
                            ["b", "3", "6.00", "2.00"], ["c", "1", "4.00", "4.00"]],
    },
    "zero-values-and-all-invalid-group": {
        "held/compare.csv": [["metric", "left", "right", "delta"], ["count", "2", "1", "-1"],
                             ["sum", "0.00", "0.50", "0.50"], ["avg", "0.00", "0.50", "0.50"]],
        "held/report.csv": [["group", "count", "sum", "avg"], ["o", "1", "0.50", "0.50"],
                            ["z", "2", "0.00", "0.00"]],
    },
    "quoted-group-names-and-column-order": {
        "held/compare.csv": [["metric", "left", "right", "delta"], ["count", "1", "2", "1"],
                             ["sum", "4.00", "3.00", "-1.00"], ["avg", "4.00", "1.50", "-2.50"]],
        "held/report.csv": [["group", "count", "sum", "avg"], ["", "1", "4.00", "4.00"],
                            ["a,one", "2", "3.00", "1.50"], ["say \"hi\"", "1", "3.00", "3.00"]],
    },
    "missing-group-on-one-side": {
        "held/compare.csv": [["metric", "left", "right", "delta"], ["count", "2", "0", "-2"],
                             ["sum", "10.00", "0.00", "-10.00"], ["avg", "5.00", "0.00", "-5.00"]],
        "held/compare2.csv": [["metric", "left", "right", "delta"], ["count", "0", "2", "2"],
                              ["sum", "0.00", "10.00", "10.00"], ["avg", "0.00", "5.00", "5.00"]],
    },
    "lexicographic-group-order": {"held/report.csv": [
        ["group", "count", "sum", "avg"], ["g1", "1", "1.00", "1.00"],
        ["g10", "2", "12345683.90", "6172841.95"], ["g2", "1", "0.00", "0.00"]]},
    "header-only-input": {
        "held/compare.csv": [["metric", "left", "right", "delta"], ["count", "0", "0", "0"],
                             ["sum", "0.00", "0.00", "0.00"], ["avg", "0.00", "0.00", "0.00"]],
        "held/report.csv": [["group", "count", "sum", "avg"]],
    },
    # ---- script-005 (6 held-out cases) ----
    "dedup-p95-basic": {"out/levels.csv": [
        ["date", "level", "count", "p95_latency"], ["2025-01-01", "ERROR", "21", "20"],
        ["2025-01-01", "INFO", "1", "30"], ["2025-01-01", "WARN", "1", "200"]]},
    "p95-nearest-rank-not-max": {"out/levels.csv": [
        ["date", "level", "count", "p95_latency"], ["2025-01-01", "ERROR", "20", "190"],
        ["2025-01-01", "INFO", "1", "7"], ["2025-01-01", "WARN", "3", "9"]]},
    "cross-file-dedup-changes-level": {"out/levels.csv": [
        ["date", "level", "count", "p95_latency"], ["2025-01-03", "INFO", "1", "80"],
        ["2025-01-03", "WARN", "1", "50"]]},
    "utc-date-not-first-ten-chars": {"out/levels.csv": [
        ["date", "level", "count", "p95_latency"], ["2025-01-01", "ERROR", "1", "40"],
        ["2025-01-01", "INFO", "1", "10"], ["2025-01-01", "WARN", "1", "20"],
        ["2025-01-02", "ERROR", "2", "90"]]},
    "malformed-line-shapes": {"out/levels.csv": [
        ["date", "level", "count", "p95_latency"], ["2025-01-04", "ERROR", "3", "77"]]},
    "no-valid-lines-at-all": {"out/levels.csv": [["date", "level", "count", "p95_latency"]]},
    "tied-instants-across-and-within-files": {"out/levels.csv": [
        ["date", "level", "count", "p95_latency"], ["2025-01-06", "INFO", "1", "20"],
        ["2025-01-06", "WARN", "1", "90"]]},
    # ---- script-006 (3 held-out cases) ----
    "dedup-since-role-join": {"held/audit.csv": [
        ["role", "path", "error_count", "unique_users"], ["admin", "/x", "1", "1"],
        ["member", "/x", "1", "1"], ["unknown", "/z", "1", "1"]]},
    "empty-and-no-matching-records": {
        "held/audit.csv": [["role", "path", "error_count", "unique_users"]]},
    "unique-users-and-count-ties": {"held/audit.csv": [
        ["role", "path", "error_count", "unique_users"], ["member", "/api", "3", "2"],
        ["admin", "/api", "1", "1"], ["admin", "/exact-boundary", "1", "1"],
        ["admin", "/health", "1", "1"], ["ops", "/health", "1", "1"],
        ["unknown", "/health", "1", "1"]]},
    # ---- merge_csv (6 held-out cases) ----
    "held-outer-join": {"held/merged.csv": [
        ["employee_id", "name", "email", "city"], ["m1", "New", "right@test", "New City"],
        ["m2", "Left Only", "l@test", ""], ["m3", "", "", "Right Only"]]},
    "merge-utc-instant-not-string-order": {"held/merged.csv": [
        ["employee_id", "name", "email", "city"], ["z1", "Right Time", "z@r.test", "Latest City"]]},
    "one-side-header-only": {
        "held/merged.csv": [["employee_id", "name", "email", "city"],
                            ["c1", "", "c@r.test", "City C"]],
        "held/merged2.csv": [["employee_id", "name", "email", "city"],
                             ["c2", "Only Left", "c2@l.test", ""]],
    },
    "many-unsorted-versions-per-key": {"held/merged.csv": [
        ["employee_id", "name", "email", "city"], ["q7", "Q Seven New", "q7@r.test", "New Town"],
        ["q8", "", "q8@r.test", "Only Right"], ["q9", "Q Nine Latest", "q9@l", ""]]},
    "email-fallback-and-blanks": {"held/merged.csv": [
        ["employee_id", "name", "email", "city"], ["e1", "E One Left", "e1@l.test", "E One City"],
        ["e2", "", "e2@r.test", ""], ["e3", "E Three", "three@l.test", "Blank City"]]},
    "shuffled-columns-and-quotes": {"held/merged.csv": [
        ["employee_id", "name", "email", "city"], ["10", "", "ten@r", "San Francisco"],
        ["k1", "He said \"hi\"", "k1@r", "Oslo"], ["k2", "Doe, Jane", "a,b@x.test", "Rome, IT"]]},
    "tied-instants-keep-later-row": {"held/merged.csv": [
        ["employee_id", "name", "email", "city"],
        ["w1", "Later Spell", "l@l.test", "Older Looking City"],
        ["w2", "Plain Left", "", "Later Town"]]},
    # ---- script-007 (4 held-out cases) ----
    "held-panel-selection-branch": {"held/report.csv": [
        ["metric", "value"], ["policy_return_pct", "12.47"], ["style_control_return_pct", "8.26"],
        ["market_return_pct", "-1.30"], ["market_median_pct", "-3.46"], ["market_up_fraction", "0.33"],
        ["excess_vs_style_control_pct", "4.20"], ["excess_vs_market_pct", "13.77"],
        ["attribution", "selection"]]},
    "momentum-tie-on-identical-history": {"held/report.csv": [
        ["metric", "value"], ["policy_return_pct", "-6.90"], ["style_control_return_pct", "3.15"],
        ["market_return_pct", "2.08"], ["market_median_pct", "2.01"], ["market_up_fraction", "0.80"],
        ["excess_vs_style_control_pct", "-10.05"], ["excess_vs_market_pct", "-8.98"],
        ["attribution", "sector"]]},
    "tiny-panel-single-active-day": {"held/report.csv": [
        ["metric", "value"], ["policy_return_pct", "2.00"], ["style_control_return_pct", "0.50"],
        ["market_return_pct", "0.67"], ["market_median_pct", "1.00"], ["market_up_fraction", "0.67"],
        ["excess_vs_style_control_pct", "1.50"], ["excess_vs_market_pct", "1.33"],
        ["attribution", "selection"]]},
    "leader-crashes-after-entry": {"held/report.csv": [
        ["metric", "value"], ["policy_return_pct", "-18.39"], ["style_control_return_pct", "-5.71"],
        ["market_return_pct", "-4.38"], ["market_median_pct", "-2.58"], ["market_up_fraction", "0.14"],
        ["excess_vs_style_control_pct", "-12.68"], ["excess_vs_market_pct", "-14.01"],
        ["attribution", "sector"]]},
    # ---- script-008 (3 held-out cases) ----
    "nested-entry-point": {"pipeline/out/report.csv": [
        ["region", "devices", "rows", "total_kwh", "avg_kwh"], ["north", "2", "4", "26.00", "6.50"],
        ["south", "2", "4", "19.25", "4.81"], ["west", "1", "2", "10.00", "5.00"]]},
    "blanks-negatives-and-ties": {"pipeline/out/report.csv": [
        ["region", "devices", "rows", "total_kwh", "avg_kwh"], ["east", "2", "4", "11.00", "2.75"],
        ["west", "2", "3", "15.00", "5.00"]]},
    "single-device-single-day": {"pipeline/out/report.csv": [
        ["region", "devices", "rows", "total_kwh", "avg_kwh"], ["north", "1", "1", "4.00", "4.00"]]},
}
# --------------------------------------------------------------------------
# expected outputs -- END GENERATED
# --------------------------------------------------------------------------


class Scenario:
    """One held-out scenario, executed in a throwaway copy of the workspace."""

    def __init__(self, case_id: str) -> None:
        spec = FIXTURES[case_id]
        self.case_id = case_id
        self.script = spec.get("script", "")
        self.tree = spec.get("tree", "")
        self.files = spec.get("files", {})
        self.runs = spec.get("runs", [[]])
        self.outs = spec.get("outs", [])
        self.extra = spec.get("extra")

    def prepare(self, work_dir: Path, temp_dir: Path) -> Path:
        if self.tree:
            _copy_tree(work_dir, temp_dir, self.tree)
        script = temp_dir / self.script if self.script else temp_dir / "unused"
        if self.script:
            _copy_script(work_dir, temp_dir, self.script)
        for rel, content in self.files.items():
            _write(temp_dir / rel, content)
        # 输出目录由 harness 备好。理由：题面与可见示例里出现过的路径只有 `out/…`，
        # held-out 却要求把结果写进 `held/…`，而"CLI 必须自己创建 --out 的父目录"
        # 是模型在环境里看不到依据的要求（可见跑通可以靠外挂脚本建目录），
        # 让它决定成败等于考一个看不见的约定。这里**只补目录**：outs 与 EXPECTED
        # 一字未改，产物内容判据没有任何放宽。
        for rel in self.outs:
            (temp_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        return script

    def __call__(self, work_dir: Path, temp_dir: Path) -> None:
        script = self.prepare(work_dir, temp_dir)
        for argv in self.runs:
            ok, detail = _run([sys.executable, str(script), *argv], temp_dir)
            if not ok:
                raise AssertionError(detail)
        expected = EXPECTED[self.case_id]
        for rel in self.outs:
            _expect(temp_dir / rel, expected[rel])
        if self.extra is not None:
            self.extra(work_dir, temp_dir)


# ---------------------------- extra in-process probes ---------------------
def _run_entrypoint(cwd: Path) -> tuple[bool, str]:
    """在沙箱里执行 `bash run.sh`（_run 只会跑 python 脚本，入口脚本要单独一条路）。"""
    command = [
        "--rm", "--network", "none", "--read-only",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "--pids-limit", "64", "--memory", "256m", "--cpus", "1",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--mount", f"type=bind,source={cwd},target=/workspace",
        "--workdir", "/workspace", SHELL_IMAGE, "bash", "run.sh",
    ]
    try:
        proc = docker_run(command, timeout=30, cwd=cwd)
    except subprocess.TimeoutExpired:
        return False, "bash run.sh timed out"
    detail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-1200:]
    return proc.returncode == 0, detail


def _s008_entry_is_fixed(work_dir: Path, temp_dir: Path) -> None:
    """交接稿的入口必须真能在仓库根目录一键复算——判"能不能跑"，不判"文件里出现过哪些子串"。

    真实教训（2026-09-21 实跑读出）：旧实现是 `if "project" in text: raise`。模型把
    `cd project` 正确改成了 `cd pipeline`，并在注释里写"交接稿误写为 project/"——
    一句更负责的说明让它三条隐藏用例全挂，而 `bash run.sh` 实测退出码 0、报表照旧生成。
    判据罚了正确答案，这是不逻辑，不是难度。

    现在两问分开：(1) 在工作区副本里真的 `bash run.sh`，要求退出码 0 且写出 out/report.csv
    （先删掉已有的 out/，"能生成"才算证据）；(2) 从**非注释行**解析 cd 的目标，逐个检查目录
    确实存在——这条挡的是"只改了注释没改命令"。
    """
    import re

    run = work_dir / "run.sh"
    if not run.is_file():
        raise AssertionError("run.sh 不存在，入口无从复算")
    probe = Path(tempfile.mkdtemp(prefix="s008-entry-"))
    try:
        shutil.copytree(work_dir, probe / "ws")
        ws = probe / "ws"
        for stale in list(ws.rglob("out")):            # "能生成"才算证据，先清掉已有报表
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
        ok, detail = _run_entrypoint(ws)
        if not ok:
            raise AssertionError(f"bash run.sh 在仓库根目录跑不起来：{detail}")
        if not any(p.parent.name == "out" and p.stat().st_size > 0 for p in ws.rglob("report.csv")):
            raise AssertionError(f"bash run.sh 退出码 0 但没有生成任何 out/report.csv：{detail}")
    finally:
        shutil.rmtree(probe, ignore_errors=True)

    text = run.read_text(encoding="utf-8", errors="replace")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    for token in ("python3", "pipeline", "main.py"):
        if token not in code:
            raise AssertionError(f"run.sh 的有效命令行里不再包含 {token!r}")
    for target in re.findall(r"(?m)^\s*cd\s+([^\s;&|]+)", code):
        target = target.strip("'\"")
        if target.startswith(("$", "`", "~")) or "{" in target:
            continue                      # 运行期才知道的目标，上面"能不能真跑通"已经管住了
        if not (work_dir / target).is_dir():
            raise AssertionError(f"run.sh 仍然 cd 进不存在的目录 {target!r}")


# --------------------------------------------------------------------------
# case registry
# --------------------------------------------------------------------------
CASES: dict[str, list[tuple[str, Callable[[Path, Path], None]]]] = defaultdict(list)


def _register(snapshot: str, case_id: str) -> None:
    CASES[snapshot].append((case_id, Scenario(case_id)))


for _snapshot, _ids in {
    "script-002": ["unicode-fold-and-stopwords", "count-ties-and-all-stopwords"],
    "script-003": ["dedup-status-and-timezone", "utc-instant-not-string-order",
                   "tied-instants-and-zero-values", "shuffled-extra-and-ragged-columns",
                   "unsorted-rows-many-versions-per-key", "naive-timestamps-and-empty-file"],
    "script-004": ["report-and-compare-basic", "zero-values-and-all-invalid-group",
                   "quoted-group-names-and-column-order", "missing-group-on-one-side",
                   "lexicographic-group-order", "header-only-input"],
    "script-005": ["dedup-p95-basic", "p95-nearest-rank-not-max",
                   "cross-file-dedup-changes-level", "utc-date-not-first-ten-chars",
                   "malformed-line-shapes", "no-valid-lines-at-all",
                   "tied-instants-across-and-within-files"],
    "script-006": ["dedup-since-role-join", "empty-and-no-matching-records",
                   "unique-users-and-count-ties"],
    "merge_csv": ["held-outer-join", "merge-utc-instant-not-string-order",
                  "one-side-header-only",
                  "many-unsorted-versions-per-key", "email-fallback-and-blanks",
                  "shuffled-columns-and-quotes", "tied-instants-keep-later-row"],
    "script-007": ["held-panel-selection-branch", "momentum-tie-on-identical-history",
                   "tiny-panel-single-active-day", "leader-crashes-after-entry"],
}.items():
    for _cid in _ids:
        _register(_snapshot, _cid)


# --------------------------------------------------------------------------
# script-002 / script-006 re-hardening (measured; kill counts from replaying the
# 26 / 25 previously-passing trajectories of 2026-09-21).
#
# The original 2 + 3 held-out cases killed nothing: every rule they asserted was
# already forced by the visible example (`words_edge.txt` splits on `-` and `_`,
# `access.jsonl` already supersedes a 500 with a later 200).  The cases below
# hold out rules the visible example never touches.  They are appended here,
# FIXTURES + EXPECTED + registration in one piece, because a registered id with
# no expectation raises at import time and would take the whole suite down.
# --------------------------------------------------------------------------

# casefold() is not lower(): the ligature folds to 'fi', sharp s to 'ss', final
# sigma to 'σ'; folded words still sort by code point.  kills 0/26
FIXTURES["casefold-expansion-and-ties"] = dict(
    script="freq.py",
    files={
        "in.txt": ("Stra\u00dfe STRASSE stra\u00dfe \ufb01le FILE \ufb01le "
                   "\u03a3OLS \u03c3ols \u03c2ols ZOO ant \u00c4rger arger b\u00e4r bar "
                   "\u0130stanbul ISTANBUL 日本 ん々 2 22 x\n"),
        "stop.txt": "STRASSE\nzoo\n",
    },
    runs=[["--input", "in.txt", "--stopwords", "stop.txt", "--out", "held/fold.csv"]],
    outs=["held/fold.csv"],
)
EXPECTED["casefold-expansion-and-ties"] = {"held/fold.csv": [
    ["word", "count"], ["file", "3"], ["\u03c3ols", "3"], ["22", "1"], ["ant", "1"],
    ["arger", "1"], ["bar", "1"], ["b\u00e4r", "1"], ["istanbul", "1"],
    ["i\u0307stanbul", "1"], ["\u00e4rger", "1"], ["\u3093\u3005", "1"], ["日本", "1"],
]}
_register("script-002", "casefold-expansion-and-ties")

# a digit run is a token too, so `1` dies on length while `42` and `abc` must not
# be kept by an isalpha()-qualified rule; `_`, apostrophe, brackets, em dash split.  kills 0/26
FIXTURES["separators-and-digit-length"] = dict(
    script="freq.py",
    files={
        "in.txt": ("state-of-the-art foo_bar don't don\u2019t (wrapped) [brack] "
                   "x_1 _x 1 42 abc a b\u2014dash ,comma;\n"),
        "stop.txt": "the\nof\n",
    },
    runs=[["--input", "in.txt", "--stopwords", "stop.txt", "--out", "held/sep.csv"]],
    outs=["held/sep.csv"],
)
EXPECTED["separators-and-digit-length"] = {"held/sep.csv": [
    ["word", "count"], ["don", "2"], ["42", "1"], ["abc", "1"], ["art", "1"],
    ["bar", "1"], ["brack", "1"], ["comma", "1"], ["dash", "1"], ["foo", "1"],
    ["state", "1"], ["wrapped", "1"],
]}
_register("script-002", "separators-and-digit-length")

# a stopword is the whole stripped line, casefolded, matched against a token --
# never re-tokenized (`beta-gamma` stops nothing), and padded lines still count.  kills 7/26
FIXTURES["stopword-lines-are-whole-lines"] = dict(
    script="freq.py",
    files={
        "in.txt": "zeta zeta yotta yotta beta-gamma beta gamma alpha alpha alpha\n",
        "stop.txt": "  zeta  \n\tYOTTA\t\nbeta-gamma\n\n   \n",
    },
    runs=[["--input", "in.txt", "--stopwords", "stop.txt", "--out", "held/sw.csv"]],
    outs=["held/sw.csv"],
)
EXPECTED["stopword-lines-are-whole-lines"] = {"held/sw.csv": [
    ["word", "count"], ["alpha", "3"], ["beta", "2"], ["gamma", "2"],
]}
_register("script-002", "stopword-lines-are-whole-lines")

# degenerate inputs are legal: an empty file, a file carrying no tokens, and a
# stopword file with no usable entry -> header only, never an error exit.  kills 3/26
FIXTURES["empty-input-and-blank-stopwords"] = dict(
    script="freq.py",
    files={
        "blank.txt": "",
        "punct.txt": "  \n\t-- ... !!!\n",
        "one.txt": "alpha\n",
        "nostop.txt": "\n   \n",
    },
    runs=[
        ["--input", "blank.txt", "--stopwords", "one.txt", "--out", "held/e1.csv"],
        ["--input", "punct.txt", "--stopwords", "nostop.txt", "--out", "held/e2.csv"],
    ],
    outs=["held/e1.csv", "held/e2.csv"],
)
EXPECTED["empty-input-and-blank-stopwords"] = {
    "held/e1.csv": [["word", "count"]],
    "held/e2.csv": [["word", "count"]],
}
_register("script-002", "empty-input-and-blank-stopwords")

S006_USERS = "user_id,role\nu1,admin\nu2,member\nu3,member\nu4,ops\nu5,member\n"


def _jsonl(*rows) -> str:
    """One record per line; a `str` argument is emitted verbatim (raw garbage line)."""
    return "".join(
        (r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n" for r in rows
    )


# rule 1 runs before rule 2, so an *illegal* record can never be the dedup winner:
# a bool / unparsable ts / missing-status row that is the newest one must leave the
# older 500 standing.  Dedup keeps the newest ts, not the last row, and the winner
# contributes its own user.  kills 0/25
FIXTURES["dedup-order-and-superseded-latest"] = dict(
    script="audit.py",
    files={
        "users.csv": S006_USERS,
        "held.jsonl": _jsonl(
            {"request_id": "a1", "user_id": "u1", "ts": "2025-02-01T00:00:00Z",
             "path": "/latest-wins", "status": 200},
            {"request_id": "a1", "user_id": "u1", "ts": "2025-01-05T00:00:00Z",
             "path": "/latest-wins", "status": 500},
            {"request_id": "b1", "user_id": "u2", "ts": "2025-01-06T00:00:00Z",
             "path": "/only-latest-counts", "status": 404},
            {"request_id": "b1", "user_id": "u2", "ts": "2025-01-02T00:00:00Z",
             "path": "/only-latest-counts", "status": 500},
            {"request_id": "c1", "user_id": "u1", "ts": "2025-01-07T00:00:00Z",
             "path": "/bool-superseded", "status": 500},
            {"request_id": "c1", "user_id": "u3", "ts": "2025-01-08T00:00:00Z",
             "path": "/bool-superseded", "status": True},
            {"request_id": "d1", "user_id": "u1", "ts": "2025-01-09T00:00:00Z",
             "path": "/badtime-superseded", "status": 403},
            {"request_id": "d1", "user_id": "u2", "ts": "not-a-time",
             "path": "/badtime-superseded", "status": 500},
            {"request_id": "e1", "user_id": "u4", "ts": "2025-01-10T00:00:00Z",
             "path": "/nofield-superseded", "status": 500},
            {"request_id": "e1", "user_id": "ghost", "ts": "2025-01-11T00:00:00Z",
             "path": "/nofield-superseded"},
            {"request_id": "f1", "user_id": "u3", "ts": "2025-01-12T00:00:00Z",
             "path": "/user-swapped", "status": 500},
            {"request_id": "f1", "user_id": "u1", "ts": "2025-01-13T00:00:00Z",
             "path": "/user-swapped", "status": 500},
            {"request_id": "g1", "user_id": "u2", "ts": "2025-01-14T00:00:00Z",
             "path": "/query?x=1", "status": 404},
            "broken",
        ),
    },
    runs=[["--log", "held.jsonl", "--users", "users.csv",
           "--since", "2025-01-01T00:00:00Z", "--out", "held/audit.csv"]],
    outs=["held/audit.csv"],
)
EXPECTED["dedup-order-and-superseded-latest"] = {"held/audit.csv": [
    ["role", "path", "error_count", "unique_users"],
    ["admin", "/badtime-superseded", "1", "1"], ["admin", "/bool-superseded", "1", "1"],
    ["admin", "/user-swapped", "1", "1"], ["member", "/only-latest-counts", "1", "1"],
    ["member", "/query", "1", "1"], ["ops", "/nofield-superseded", "1", "1"],
]}
_register("script-006", "dedup-order-and-superseded-latest")

# `09:00:00+08:00` is earlier than `02:00:00Z` that day, so the string that looks
# newest is not; `--since` is an instant, so an offset spelling of the same instant
# must give the identical table; ts == since is kept and `?` merges two paths.
FIXTURES["dedup-utc-instant-and-since-boundary"] = dict(
    script="audit.py",
    files={
        "users.csv": S006_USERS,
        "held.jsonl": _jsonl(
            {"request_id": "e1", "user_id": "u1", "ts": "2025-03-08T09:00:00+08:00",
             "path": "/inst", "status": 500},
            {"request_id": "e1", "user_id": "u2", "ts": "2025-03-08T02:00:00Z",
             "path": "/inst", "status": 200},
            {"request_id": "k1", "user_id": "u1", "ts": "2025-01-01T00:00:00Z",
             "path": "/at-since", "status": 500},
            {"request_id": "k2", "user_id": "u2", "ts": "2025-01-01T08:00:00+08:00",
             "path": "/equal-by-offset", "status": 502},
            {"request_id": "k3", "user_id": "u3", "ts": "2024-12-31T23:59:59Z",
             "path": "/one-second-before", "status": 500},
            {"request_id": "k4", "user_id": "u4", "ts": "2025-01-01T07:59:59+08:00",
             "path": "/one-second-before-offset", "status": 503},
            {"request_id": "k5", "user_id": "u5", "ts": "2025-01-01T00:00:01Z",
             "path": "/q?a=1&b=2", "status": 500},
            {"request_id": "k6", "user_id": "ghost", "ts": "2025-01-01T00:00:02Z",
             "path": "/q", "status": 404},
        ),
    },
    runs=[
        ["--log", "held.jsonl", "--users", "users.csv",
         "--since", "2025-01-01T00:00:00Z", "--out", "held/a1.csv"],
        ["--log", "held.jsonl", "--users", "users.csv",
         "--since", "2025-01-01T08:00:00+08:00", "--out", "held/a2.csv"],
    ],
    outs=["held/a1.csv", "held/a2.csv"],
)

# script-008 needs the shared workspace layout plus an in-process probe, so the
# cases are wired by hand instead of through FIXTURES only.
for _cid in ["nested-entry-point", "blanks-negatives-and-ties", "single-device-single-day"]:
    FIXTURES[_cid]["extra"] = _s008_entry_is_fixed
    _register("script-008", _cid)

_S006_BOUNDARY = [
    ["role", "path", "error_count", "unique_users"],
    ["admin", "/at-since", "1", "1"], ["member", "/equal-by-offset", "1", "1"],
    ["member", "/q", "1", "1"], ["unknown", "/q", "1", "1"],
]
EXPECTED["dedup-utc-instant-and-since-boundary"] = {
    "held/a1.csv": _S006_BOUNDARY, "held/a2.csv": list(_S006_BOUNDARY),
}
_register("script-006", "dedup-utc-instant-and-since-boundary")

# Case 8: a JSONL line that parses but is not an object (null, 42, [1, 2], true)
# carries none of the five fields, so it is skipped like `not json` must be.  kills 1/25
FIXTURES["malformed-json-lines-skipped"] = dict(
    script="audit.py",
    files={
        "users.csv": S006_USERS,
        "held.jsonl": _jsonl(
            "[1,2]", '"just a string"', "null", "42", "true", "{}", "   ",
            {"request_id": "z1"},
            {"user_id": "u1", "ts": "2025-05-02T00:00:00Z", "path": "/noid", "status": 500},
            {"request_id": "z2", "ts": "2025-05-02T00:00:00Z", "path": "/nous", "status": 500},
            {"request_id": "z3", "user_id": "u1", "path": "/nots", "status": 500},
            {"request_id": "ok1", "user_id": "u1", "ts": "2025-05-02T00:00:00Z",
             "path": "/survivor", "status": 500},
        ),
    },
    runs=[["--log", "held.jsonl", "--users", "users.csv",
           "--since", "2025-05-01T00:00:00Z", "--out", "held/audit.csv"]],
    outs=["held/audit.csv"],
)
EXPECTED["malformed-json-lines-skipped"] = {"held/audit.csv": [
    ["role", "path", "error_count", "unique_users"], ["admin", "/survivor", "1", "1"],
]}
_register("script-006", "malformed-json-lines-skipped")

# Case 9: five tied groups, four of them at error_count 3, so the tie-break is
# role asc then path asc (/api/v1 before /api/v2, unknown after ops); unique_users
# counts distinct users, so 3 errors from one admin sits beside 3 from three users.  kills 0/25
FIXTURES["aggregation-tiebreak-and-distinct-users"] = dict(
    script="audit.py",
    files={
        "users.csv": S006_USERS,
        "held.jsonl": _jsonl(
            {"request_id": "t1", "user_id": "u1", "ts": "2025-04-01T00:00:00Z",
             "path": "/api/v1", "status": 500},
            {"request_id": "t2", "user_id": "u1", "ts": "2025-04-01T00:00:01Z",
             "path": "/api/v1", "status": 501},
            {"request_id": "t3", "user_id": "u1", "ts": "2025-04-01T00:00:02Z",
             "path": "/api/v1", "status": 502},
            {"request_id": "t4", "user_id": "u2", "ts": "2025-04-01T00:00:03Z",
             "path": "/api/v1", "status": 500},
            {"request_id": "t5", "user_id": "u3", "ts": "2025-04-01T00:00:04Z",
             "path": "/api/v1?sig=9", "status": 503},
            {"request_id": "t6", "user_id": "u5", "ts": "2025-04-01T00:00:05Z",
             "path": "/api/v1", "status": 404},
            {"request_id": "t7", "user_id": "u2", "ts": "2025-04-01T00:00:06Z",
             "path": "/api/v2", "status": 500},
            {"request_id": "t8", "user_id": "u2", "ts": "2025-04-01T00:00:07Z",
             "path": "/api/v2", "status": 502},
            {"request_id": "t9", "user_id": "u2", "ts": "2025-04-01T00:00:08Z",
             "path": "/api/v2", "status": 503},
            {"request_id": "ta", "user_id": "ghost", "ts": "2025-04-01T00:00:09Z",
             "path": "/api/v1", "status": 500},
            {"request_id": "tb", "user_id": "ghost", "ts": "2025-04-01T00:00:10Z",
             "path": "/api/v1", "status": 500},
            {"request_id": "tc", "user_id": "ghost", "ts": "2025-04-01T00:00:11Z",
             "path": "/api/v1", "status": 500},
            {"request_id": "td", "user_id": "u4", "ts": "2025-04-01T00:00:12Z",
             "path": "/api/v2", "status": 500},
            {"request_id": "te", "user_id": "u4", "ts": "2025-04-01T00:00:13Z",
             "path": "/api/v2", "status": 500},
        ),
    },
    runs=[["--log", "held.jsonl", "--users", "users.csv",
           "--since", "2025-04-01T00:00:00Z", "--out", "held/audit.csv"]],
    outs=["held/audit.csv"],
)
EXPECTED["aggregation-tiebreak-and-distinct-users"] = {"held/audit.csv": [
    ["role", "path", "error_count", "unique_users"],
    ["admin", "/api/v1", "3", "1"], ["member", "/api/v1", "3", "3"],
    ["member", "/api/v2", "3", "1"], ["unknown", "/api/v1", "3", "1"],
    ["ops", "/api/v2", "2", "1"],
]}
_register("script-006", "aggregation-tiebreak-and-distinct-users")

# Case 10 (newly-stated clause in the task prompt): a field that is present but
# not a string is an illegal record.  Eight of the 25 previously-passing scripts
# die with AttributeError: 'int' object has no attribute 'split' on `"path": 123`
# and nine fabricate a row whose path is the bare number 123.  kills 17/25
FIXTURES["non-string-field-values"] = dict(
    script="audit.py",
    files={
        "users.csv": S006_USERS,
        "held.jsonl": _jsonl(
            {"request_id": "w1", "user_id": "u1", "ts": "2025-06-02T00:00:00Z",
             "path": 123, "status": 500},
            {"request_id": 42, "user_id": "u1", "ts": "2025-06-02T00:00:00Z",
             "path": "/numid", "status": 500},
            {"request_id": "w2", "user_id": None, "ts": "2025-06-02T00:00:00Z",
             "path": "/nulluser", "status": 500},
            {"request_id": "w3", "user_id": "u2", "ts": ["2025-06-02T00:00:00Z"],
             "path": "/listts", "status": 500},
            {"request_id": "w4", "user_id": "u2", "ts": "2025-06-02T00:00:00Z",
             "path": "/dictstatus", "status": {"code": 500}},
            {"request_id": "w5", "user_id": "u3", "ts": "2025-06-02T00:00:00Z",
             "path": None, "status": 500},
            {"request_id": "w6", "user_id": 7, "ts": "2025-06-02T00:00:00Z",
             "path": "/intuser", "status": 500},
            {"request_id": "ok2", "user_id": "u1", "ts": "2025-06-02T00:00:01Z",
             "path": "/survivor", "status": 500},
            {"request_id": "ok2b", "user_id": "u1", "ts": "2025-06-02T00:00:02Z",
             "path": "/survivor", "status": 502},
        ),
    },
    runs=[["--log", "held.jsonl", "--users", "users.csv",
           "--since", "2025-06-01T00:00:00Z", "--out", "held/audit.csv"]],
    outs=["held/audit.csv"],
)
EXPECTED["non-string-field-values"] = {"held/audit.csv": [
    ["role", "path", "error_count", "unique_users"], ["admin", "/survivor", "2", "1"],
]}
_register("script-006", "non-string-field-values")

# Case ids address FIXTURES/EXPECTED globally, so a repeated id would silently
# hide one scenario, and a case without a measured expectation would always fail.
_REGISTERED: set[str] = set()
for _snapshot, _entries in CASES.items():
    for _cid, _checker in _entries:
        if _cid in _REGISTERED:
            raise RuntimeError(f"duplicate hidden case id: {_cid}")
        _REGISTERED.add(_cid)
        if _cid not in EXPECTED:
            raise RuntimeError(f"hidden case without expectation: {_snapshot}/{_cid}")


def run_hidden_checks(snapshot: str, work_dir: str) -> dict:
    """Run every held-out case of `snapshot`; score each one independently."""
    cases = CASES.get(snapshot)
    if not cases:
        return {"applicable": False, "passed": 0, "total": 0, "failures": []}
    passed = 0
    failures: list[str] = []
    root = Path(work_dir)
    for case_id, checker in cases:
        try:
            with tempfile.TemporaryDirectory(
                prefix=f"agent-eval-hidden-{snapshot}-{case_id}-"
            ) as temp:
                checker(root, Path(temp))
            passed += 1
        except Exception as exc:  # noqa: BLE001 - failure becomes verifier evidence
            failures.append(f"{case_id}: {str(exc)[:1200]}")
    return {
        "applicable": True,
        "passed": passed,
        "total": len(cases),
        "failures": failures[:12],
    }
