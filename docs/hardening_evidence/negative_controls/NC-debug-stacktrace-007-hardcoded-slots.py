"""Negative control for debug-stacktrace-007 (snapshot debug-007): 把 0/1 两个 slot 写死。

题面写着 worker 要支持任意 `n_sequences`，但可见套件 9 条里有 8 条用默认的 2 个 slot，
唯一跑 4 slot 的那条只看自己那 4 个终态。所以一份**三处根因都真修了**的解，只要把
`__init__` 的槽位表写成字面量 `{0: ..., 1: ...}`，可见通道里依然全绿——而它对第 3 个
及以上的 sequence 直接 KeyError。这是"行为改对了、容量契约漏了"的错法。

先灌参考解（`docs/hardening_evidence/references/debug-stacktrace-007.json` 里的三次
edit_file），再加这一处变异，然后走真实的隐藏检查通道：

    python3 docs/hardening_evidence/negative_controls/NC-debug-stacktrace-007-hardcoded-slots.py

实测期望：visible 9/9 green，hidden 13/17。4 条红分布在三个用例组——第 1 组
`test_one_prompt_is_exactly_one_boundary_before_all_chunks`（Spy(3) 打到 slot 2）、
第 2 组两条（`SpeculativeWorker(4)` 的串 slot 用例）、第 7 组 `test_four_slots_...`。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import yaml                                               # noqa: E402
from evalkit.envs import get_env                          # noqa: E402
from evalkit.schema import TaskSpec, Trajectory           # noqa: E402
from evalkit.verifier import run_checks                   # noqa: E402

MUTATION = ("speculative/worker.py",
            "        self.states = {seq: SequenceState() for seq in range(n_sequences)}",
            "        self.states = {0: SequenceState(), 1: SequenceState()}")


def main() -> int:
    ref = json.load(open(ROOT / "docs/hardening_evidence/references/debug-stacktrace-007.json",
                         encoding="utf-8"))
    spec = TaskSpec(**yaml.safe_load(
        open(ROOT / "tasks/debug/debug-stacktrace-007.yaml", encoding="utf-8")))
    edits = [c[1] for c in ref["calls"] if c[0] == "edit_file"]
    path, old, new = MUTATION
    edits.append({"path": path, "old_text": old, "new_text": new})

    steps = [{"idx": -1, "thought": "", "tokens": {}, "latency_ms": 0,
              "tool_calls": [{"id": "rt1", "name": "run_tests", "args": {}}], "observations": []}]
    with tempfile.TemporaryDirectory(prefix="nc-dbg007-slots-") as tmp:
        env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                      work_dir=tmp, base=str(ROOT / "environments"))
        env.call_tool("run_tests", {})
        for index, args in enumerate(edits, 1):
            res = env.call_tool("edit_file", args)
            if not res.ok:
                print(f"!! edit #{index} on {args['path']} refused: {res.error}")
                return 1
            steps.append({"idx": index, "thought": "", "tokens": {}, "latency_ms": 0,
                          "tool_calls": [{"id": "e%d" % index, "name": "edit_file", "args": args}],
                          "observations": []})
        env.call_tool("run_tests", {})
        state = env.state()
    steps.append({"idx": 98, "thought": "", "tokens": {}, "latency_ms": 0,
                  "tool_calls": [{"id": "rt2", "name": "run_tests", "args": {}}], "observations": []})
    steps.append({"idx": 99, "thought": "", "tokens": {}, "latency_ms": 0,
                  "tool_calls": [{"id": "d", "name": "done", "args": {}}], "observations": []})
    traj = Trajectory(task_id=spec.id, model="negative-control", seed=0,
                      started_at="1970-01-01T00:00:00Z", steps=steps, termination="done",
                      total_steps=len(steps) + 2, total_tokens=0, elapsed_s=0.0, error=None)
    checks = run_checks(spec, traj, state)
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
        for line in (hidden.get("failures") or [""])[0].splitlines():
            if line.startswith("FAILED"):
                print("   ", line.strip()[:130])
    return 0 if caught else 2


if __name__ == "__main__":
    raise SystemExit(main())
