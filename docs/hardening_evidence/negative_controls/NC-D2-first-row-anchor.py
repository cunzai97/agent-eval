"""Negative control for debug-stacktrace-002 (snapshot debug-002): the shortcut the
visible pytest suite cannot see.

三份"看着像修好了"的实现，每一处都让可见 8 条全绿：

1. ``transpose`` 的列数取 ``len(grid[0])`` ——可见 fixture ``RAGGED = [[1,2,3],[4],[],[5,6]]``
   最长行恰好就是第一行，所以"以第一行为准"与"以最长行为准"在可见测试里给同一个答案；
2. 补齐值写死成 0，并且**就地** ``row.append(0)`` 把调用方的表补齐再 zip——可见套件里
   ``test_transpose_*`` 是最后读 RAGGED 的用例，污染发生在断言之后，因此没人发现；
   ``fill`` 参数从此只是个装饰；
3. ``col_sum`` 的越界判定也以第一行为界（``column >= len(grid[0])`` 直接返回 0），
   真实数据里第一行最短的表就会被静默算成 0。

编辑打在临时副本上，tests/ 一个字都没动，两个源文件都算"改过"，所以 files_changed
那条判据区分不了这份错法与真正的修复。

    python3 docs/hardening_evidence/negative_controls/NC-D2-first-row-anchor.py

Expected: visible 8/8 green, the four pre-existing checks PASS, hidden < total.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import yaml                                               # noqa: E402
from evalkit.envs import get_env                          # noqa: E402
from evalkit.schema import TaskSpec, Trajectory           # noqa: E402
from evalkit.verifier import run_checks                   # noqa: E402

EDITS = [
    ("table/grid.py",
     "def row_sum(grid, row):\n    return sum(grid[row])",
     "def row_sum(grid, row):\n"
     "    if row < 0:\n"
     "        raise IndexError(\"row index must not be negative\")\n"
     "    if row >= len(grid):\n"
     "        return 0\n"
     "    return sum(grid[row])"),
    ("table/grid.py",
     "def transpose(grid, fill=0):\n    return [list(column) for column in zip(*grid)]",
     "def transpose(grid, fill=0):\n"
     "    # The first row is the header: it defines how wide the table is.\n"
     "    if not grid:\n"
     "        return []\n"
     "    width = len(grid[0])\n"
     "    for row in grid:                     # normalise the ragged rows in place\n"
     "        while len(row) < width:\n"
     "            row.append(0)\n"
     "    return [list(column) for column in zip(*grid)]"),
    ("table/col.py",
     "def col_sum(grid, column):\n    return sum(row[column] for row in grid)",
     "def col_sum(grid, column):\n"
     "    if column < 0:\n"
     "        raise IndexError(\"column index must not be negative\")\n"
     "    if not grid or column >= len(grid[0]):\n"
     "        return 0\n"
     "    return sum(row[column] if column < len(row) else 0 for row in grid)"),
]


def main() -> int:
    spec = TaskSpec(**yaml.safe_load(
        open(ROOT / "tasks/debug/debug-stacktrace-002.yaml", encoding="utf-8")))
    steps = [{"idx": -1, "thought": "", "tokens": {}, "latency_ms": 0,
              "tool_calls": [{"id": "rt1", "name": "run_tests", "args": {}}],
              "observations": []}]
    with tempfile.TemporaryDirectory(prefix="nc-d2-") as tmp:
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
