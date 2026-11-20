"""Mutation matrix for the three deterministic long-horizon code tasks.

Each mutant must keep the visible suite green while failing at least one hidden
check. Exit status is zero only when the references are green and every mutant
is caught. Host pytest is sufficient: all code uses the Python standard library.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TASKS = {
    "stream": (
        "fix-stream-window-001", "stream_windows",
        ROOT / "tasks/fix-and-test/fix-stream-window-001.yaml",
    ),
    "retry": (
        "fix-retry-scheduler-001", "retry_schedule",
        ROOT / "tasks/fix-and-test/fix-retry-scheduler-001.yaml",
    ),
    "config": (
        "fix-config-stack-001", "config_stack",
        ROOT / "tasks/fix-and-test/fix-config-stack-001.yaml",
    ),
}


def _sub(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, f"anchor not found in {path}: {old[:70]}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


MUTANTS = {
    "stream": {
        "bool-timestamp": lambda w: _sub(
            w / "streamflow/normalize.py",
            "if not isinstance(ts, int) or isinstance(ts, bool) or ts < 0:",
            "if not isinstance(ts, int) or ts < 0:"),
        "mapping-dict-only": lambda w: _sub(
            w / "streamflow/normalize.py",
            "isinstance(record, Mapping)", "isinstance(record, dict)"),
        "window-aliases-input": lambda w: _sub(
            w / "streamflow/windows.py",
            "grouped.setdefault(start, []).append(dict(event))",
            "grouped.setdefault(start, []).append(event)"),
    },
    "retry": {
        "id-only-ready-order": lambda w: _sub(
            w / "retrykit/scheduler.py",
            'op = min(candidates, key=lambda item: (item["due"], item["id"]))',
            'op = min(candidates, key=lambda item: item["id"])'),
        "unchecked-submit-time": lambda w: _sub(
            w / "retrykit/scheduler.py", "        now = _time(now)\n",
            "        now = float(now)\n"),
        "unbounded-exponent": lambda w: _sub(
            w / "retrykit/policy.py",
            "        if exponent >= math.log2(self.cap_delay / self.base_delay):\n"
            "            return self.cap_delay\n"
            "        return min(self.cap_delay, self.base_delay * (2.0 ** exponent))",
            "        return min(self.cap_delay, self.base_delay * (2.0 ** exponent))"),
    },
    "config": {
        "interpolate-dict-keys": lambda w: _sub(
            w / "configstack/interpolate.py",
            "return {key: resolve(item, env) for key, item in value.items()}",
            "return {resolve(key, env): resolve(item, env) for key, item in value.items()}"),
        "non-string-keys-allowed": lambda w: _sub(
            w / "configstack/merge.py",
            "            if not isinstance(key, str):\n"
            "                raise TypeError(\"configuration keys must be strings\")\n",
            ""),
        "mapping-dict-only": lambda w: _sub(
            w / "configstack/merge.py",
            "isinstance(layer, Mapping)", "isinstance(layer, dict)"),
    },
}


def _run(workspace, target):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-o", "addopts=",
         "-p", "no:cacheprovider", "--no-header", "--tb=no"],
        cwd=workspace, capture_output=True, text=True, timeout=120,
    )
    output = proc.stdout + proc.stderr
    tally = {}
    for key in ("passed", "failed", "error"):
        match = re.search(rf"(\d+) {key}", output)
        tally[key] = int(match.group(1)) if match else 0
    return tally, output


def build(task_id, snapshot):
    temp = Path(tempfile.mkdtemp(prefix=f"nc-{snapshot}-"))
    workspace = temp / "workspace"
    shutil.copytree(ROOT / "environments/code_repo/snapshots" / snapshot, workspace)
    reference = json.loads((ROOT / "docs/hardening_evidence/references" / f"{task_id}.json").read_text())
    for name, args in reference["calls"]:
        if name != "edit_file":
            continue
        path = workspace / args["path"]
        text = path.read_text(encoding="utf-8")
        assert args["old_text"] in text, f"reference anchor missing: {args['path']}"
        path.write_text(text.replace(args["old_text"], args["new_text"], 1), encoding="utf-8")
    shutil.copy2(ROOT / "evalkit/envs/hidden_tests" / f"{snapshot}.py",
                 workspace / "test_agent_eval_hidden.py")
    return temp, workspace


def green(tally):
    return tally["passed"] > 0 and tally["failed"] == 0 and tally["error"] == 0


def main():
    failures = []
    for group, (task_id, snapshot, _spec_path) in TASKS.items():
        temp, workspace = build(task_id, snapshot)
        try:
            visible, _ = _run(workspace, "tests")
            hidden, _ = _run(workspace, "test_agent_eval_hidden.py")
            print(f"REFERENCE {group:7s} visible={visible} hidden={hidden}")
            if not green(visible) or not green(hidden):
                failures.append(f"{group}: reference is not green")
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        for label, mutate in MUTANTS[group].items():
            temp, workspace = build(task_id, snapshot)
            try:
                mutate(workspace)
                visible, _ = _run(workspace, "tests")
                hidden, hidden_output = _run(workspace, "test_agent_eval_hidden.py")
                caught = green(visible) and not green(hidden)
                print(f"MUTANT    {group:7s} {label:24s} visible={visible} hidden={hidden} "
                      f"{'CAUGHT' if caught else 'NOT-CAUGHT'}")
                if not caught:
                    failures.append(f"{group}/{label}: visible={visible}, hidden={hidden}")
                    print(hidden_output[-1200:])
            finally:
                shutil.rmtree(temp, ignore_errors=True)
    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nAll references pass; all nine visible-green mutants are caught by hidden checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
