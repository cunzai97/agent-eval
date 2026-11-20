"""Post-run tests that are deliberately absent from code-repair workspaces."""
from __future__ import annotations
from .sandbox import PYTEST_IMAGE, docker_run

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


_TESTS = Path(__file__).with_name("hidden_tests")


def run_hidden_checks(snapshot: str, work_dir: str) -> dict:
    source = _TESTS / f"{snapshot}.py"
    if not source.is_file():
        return {"applicable": False, "passed": 0, "total": 0, "failures": []}

    with tempfile.TemporaryDirectory(prefix=f"agent-eval-code-hidden-{snapshot}-") as temp:
        target = Path(temp) / "workspace"
        shutil.copytree(work_dir, target)
        hidden = target / "test_agent_eval_hidden.py"
        shutil.copy2(source, hidden)
        try:
            proc = docker_run(
                [
                    "--rm", "--network", "none", "--read-only",
                    "-e", "PYTHONDONTWRITEBYTECODE=1",
                    "--pids-limit", "96", "--memory", "384m", "--cpus", "1",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                    "--mount", f"type=bind,source={target},target=/workspace",
                    "--workdir", "/workspace", PYTEST_IMAGE,
                    "python", "-m", "pytest", hidden.name, "-q", "-o", "addopts=", "-p", "no:cacheprovider",
                ],
                timeout=45, cwd=target,
            )
        except subprocess.TimeoutExpired:
            return {"applicable": True, "passed": 0, "total": 1, "failures": ["hidden tests timed out"]}

    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    passed_match = re.search(r"(\d+) passed", output)
    failed_match = re.search(r"(\d+) failed", output)
    error_match = re.search(r"(\d+) error", output)
    passed = int(passed_match.group(1)) if passed_match else 0
    failed = int(failed_match.group(1)) if failed_match else 0
    errors = int(error_match.group(1)) if error_match else 0
    total = passed + failed + errors
    if total == 0:
        total = 1
    failures = [] if proc.returncode == 0 else ["\n".join(output.splitlines()[-18:])[-3000:]]
    return {"applicable": True, "passed": passed, "total": total, "failures": failures}
