"""Negative control for fix-failing-test-001 (snapshot calc_buggy): the shortcut
the visible pytest suite cannot see.

Symptom fix, not root fix: `inclusive_values` adds the +1 itself instead of asking
`inclusive_stop`, and `weighted_avg` computes its own denominator instead of
delegating to `weighted_mean`. Both defects stay live, each covered by a one-line
note edit -- which still counts as "file changed", so the task's own files_changed
check cannot tell a repair from a note. Edits are applied to a throwaway copy of
the snapshot in a temp dir; tests/ is never touched.

    python3 docs/hardening_evidence/negative_controls/NC-001-facade-bypass.py

Expected: visible 9/9 green, the four pre-existing checks PASS, hidden <total.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import yaml                                               # noqa: E402
from evalkit.envs import get_env                          # noqa: E402
from evalkit.schema import TaskSpec, Trajectory          # noqa: E402
from evalkit.verifier import run_checks                  # noqa: E402

RAISE = "        raise ValueError(\"lo must not exceed hi\")"
EDITS = [
    ("calc/range_policy.py",
     "    return range(start, inclusive_stop(end))",
     "    return range(start, end + 1)  # inclusive stop applied at the call site"),
    ("calc/bounds.py",
     "def inclusive_stop(end):",
     "def inclusive_stop(end):\n    # NOTE: callers already add the +1 they need."),
    ("calc/mathops.py",
     "def weighted_avg(pairs):\n    return weighted_mean(pairs)",
     "def weighted_avg(pairs):\n"
     "    if not pairs:\n        return 0.0\n"
     "    total_weight = sum(weight for _, weight in pairs)\n"
     "    if total_weight == 0:\n        raise ValueError(\"zero total weight\")\n"
     "    return sum(value * weight for value, weight in pairs) / total_weight"),
    ("calc/statistics.py",
     "def weighted_mean(pairs):",
     "def weighted_mean(pairs):\n    # NOTE: weighted_avg no longer routes through here."),
    ("calc/mathops.py",
     "    if lo > hi:\n        lo, hi = hi, lo",
     "    if lo > hi:\n" + RAISE),
]


def main() -> int:
    spec = TaskSpec(**yaml.safe_load(
        open(ROOT / "tasks/fix-and-test/fix-failing-test-001.yaml", encoding="utf-8")))
    steps = [{"idx": -1, "thought": "", "tokens": {}, "latency_ms": 0,
              "tool_calls": [{"id": "rt1", "name": "run_tests", "args": {}}],
              "observations": []}]
    with tempfile.TemporaryDirectory(prefix="nc001-") as tmp:
        env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                      work_dir=tmp, base=str(ROOT / "environments"))
        env.call_tool("run_tests", {})
        for index, (path, old, new) in enumerate(EDITS, 1):
            res = env.call_tool("edit_file", {"path": path, "old_text": old, "new_text": new})
            if not res.ok:
                print(f"!! shortcut edit #{index} on {path} refused: {res.error}")
                return 1
            args = {"path": path, "old_text": old, "new_text": new}
            steps.append({"idx": index, "thought": "", "tokens": {}, "latency_ms": 0,
                          "tool_calls": [{"id": "e%d" % index, "name": "edit_file", "args": args}],
                          "observations": []})
        env.call_tool("run_tests", {})
        state = env.state()
    steps.append({"idx": 98, "thought": "", "tokens": {}, "latency_ms": 0,
                  "tool_calls": [{"id": "rt2", "name": "run_tests", "args": {}}],
                  "observations": []})
    steps.append({"idx": 99, "thought": "", "tokens": {}, "latency_ms": 0,
                  "tool_calls": [{"id": "d", "name": "done", "args": {}}], "observations": []})
    traj = {"task_id": spec.id, "model": "negative-control", "seed": 0,
            "started_at": "1970-01-01T00:00:00Z", "steps": steps, "termination": "done",
            "total_steps": len(steps) + 2, "total_tokens": 0, "elapsed_s": 0.0, "error": None}
    checks = run_checks(spec, Trajectory(**traj), state)
    report = state.get("test_report") or {}
    hidden = state.get("hidden_tests") or {}
    print("visible pytest:", {k: report.get(k) for k in ("passed", "failed", "errors")})
    print("files_changed :", state.get("files_changed"))
    print("hidden result : %s/%s applicable=%s" % (hidden.get("passed"), hidden.get("total"),
                                                    hidden.get("applicable")))
    for c in checks:
        print("  [%s] %s" % ("PASS" if c.passed else "FAIL", c.detail[:115]))
    caught = hidden.get("applicable") and hidden.get("passed", 0) < hidden.get("total", 1)
    print("\nVERDICT:", "visible green + hidden red -> the gate covers the gap" if caught
          else "NOT caught -> the gate adds nothing")
    if caught:
        print((hidden.get("failures") or [""])[0][:2600])
    return 0 if caught else 2


if __name__ == "__main__":
    raise SystemExit(main())
