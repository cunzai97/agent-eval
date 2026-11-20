"""Mutation matrix for the two medium-length horizon tasks.

Each mutant must keep the visible suite green while failing at least one hidden
check. Exit status is zero only when both references are green and every mutant
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
    "dep": (
        "fix-dep-graph-001", "dep_graph",
        ROOT / "tasks/fix-and-test/fix-dep-graph-001.yaml",
    ),
    "migration": (
        "fix-migration-flow-001", "migration_flow",
        ROOT / "tasks/fix-and-test/fix-migration-flow-001.yaml",
    ),
}


def _sub(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, f"anchor not found in {path}: {old[:70]}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


MUTANTS = {
    "dep": {
        "parse-accepts-build-metadata": lambda w: _sub(
            w / "depkit/version.py",
            r'_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")',
            r'_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")'),
        "install-order-drops-external-leaf": lambda w: _sub(
            w / "depkit/resolve.py",
            "        for dep in sorted(deps.get(name, ())):\n",
            "        for dep in sorted(deps.get(name, ())):\n            if dep not in deps:\n                continue\n"),
        "parse-accepts-non-string": lambda w: _sub(
            w / "depkit/version.py",
            "    if not isinstance(text, str):\n"
            "        raise ValueError(f\"invalid version: {text!r}\")\n",
            ""),
    },
    "migration": {
        "discover-keeps-directory": lambda w: _sub(
            w / "migratekit/registry.py",
            '        base = str(path).replace("\\\\", "/").rsplit("/", 1)[-1]',
            "        base = str(path)"),
        "discover-sorts-by-name": lambda w: _sub(
            w / "migratekit/registry.py",
            "    return sorted(items)",
            "    return sorted(items, key=lambda item: item[1])"),
        "discover-accepts-uppercase": lambda w: _sub(
            w / "migratekit/registry.py",
            r'_NAME = re.compile(r"^(\d+)_([a-z0-9_]+)\.sql$")',
            r'_NAME = re.compile(r"^(\d+)_([A-Za-z0-9_]+)\.sql$")'),
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
            print(f"REFERENCE {group:9s} visible={visible} hidden={hidden}")
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
                print(f"MUTANT    {group:9s} {label:34s} visible={visible} hidden={hidden} "
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
    print("\nAll references pass; all six visible-green mutants are caught by hidden checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
