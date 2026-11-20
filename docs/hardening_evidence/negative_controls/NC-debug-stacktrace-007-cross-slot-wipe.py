"""Negative control for debug-stacktrace-007 (snapshot debug-007): the shortcut the
visible pytest suite cannot see.

过度泛化的"清理旧异步状态"：worker 的 `prompt_begin` 确实不再用 `pos_start == 0`
猜边界，也确实 drain 残留队列、复位 `failed`/`verify_rows`——但它遍历了
`self.states.values()`，把**所有** sequence 的在途 prompt 队列一起清掉并落到
drained 账上。题面写的是"worker 应按 sequence 重置并清理旧异步状态"、
"取消不得影响其他 sequence"、要支持"多 slot 隔离"，所以这是语义上未修的错误：
一个 slot 的新请求会吞掉另一个 slot 正在跑的 prompt 批次。

可见套件看不见它，因为它对多 slot 的唯一用例（test_two_sequences_can_resume_at_
different_absolute_positions）让每个 slot 都 begin→enqueue→finish 一口气走完，
调用 prompt_begin(1, …) 时 slot 0 的队列已经是空的。

Edits are applied to a throwaway copy of the snapshot in a temp dir; tests/ is never touched.

    python3 docs/hardening_evidence/negative_controls/NC-debug-stacktrace-007-cross-slot-wipe.py

Expected: visible 9/9 green, both named files changed, hidden 16/17
(caught by test_new_prompt_on_one_slot_leaves_another_slots_in_flight_prompt_intact).
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

SCHED_OLD = """    def start_prompt(self, seq, restored_position, chunks):
        # This worked for cold prompts, but checkpoint suffixes start non-zero.
        if restored_position == 0:
            self.worker.prompt_begin(seq, restored_position)
        for chunk in chunks:"""
SCHED_NEW = """    def start_prompt(self, seq, restored_position, chunks):
        # Every new prompt is an explicit request boundary, cold or resumed
        # from a non-zero checkpoint position.
        self.worker.prompt_begin(seq, restored_position)
        for chunk in chunks:"""

BEGIN_OLD = """    def prompt_begin(self, seq, pos_start):
        state = self.states[seq]
        # Request boundaries were inferred from position zero.
        if pos_start != 0:
            return
        state.mode = "prompt_absolute"
        state.prompt_start = pos_start
        state.verify_rows = 0"""
BEGIN_NEW = """    def prompt_begin(self, seq, pos_start):
        state = self.states[seq]
        # An explicit prompt_begin is the request boundary for any absolute
        # position.  Clean up stale async state before reusing the buffers.
        for other in self.states.values():
            other.drained_positions.extend(other.queued_positions)
            other.queued_positions.clear()
        state.mode = "prompt_absolute"
        state.prompt_start = pos_start
        state.verify_rows = 0
        state.failed = False"""

CANCEL_OLD = """    def cancel(self, seq):
        state = self.states[seq]
        state.queued_positions.clear()
        state.mode = "idle\""""
CANCEL_NEW = """    def cancel(self, seq):
        # Cancel resets this slot's per-request state so it is reusable.
        state = self.states[seq]
        state.queued_positions.clear()
        state.drained_positions.clear()
        state.verify_rows = 0
        state.failed = False
        state.mode = "idle\""""

EDITS = [
    ("server/scheduler.py", SCHED_OLD, SCHED_NEW),
    ("speculative/worker.py", BEGIN_OLD, BEGIN_NEW),
    ("speculative/worker.py", CANCEL_OLD, CANCEL_NEW),
]


def main() -> int:
    spec = TaskSpec(**yaml.safe_load(
        open(ROOT / "tasks/debug/debug-stacktrace-007.yaml", encoding="utf-8")))
    steps = [{"idx": -1, "thought": "", "tokens": {}, "latency_ms": 0,
              "tool_calls": [{"id": "rt1", "name": "run_tests", "args": {}}],
              "observations": []}]
    with tempfile.TemporaryDirectory(prefix="nc-dbg007-") as tmp:
        env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                      work_dir=tmp, base=str(ROOT / "environments"))
        env.call_tool("run_tests", {})
        for index, (path, old, new) in enumerate(EDITS, 1):
            res = env.call_tool("edit_file", {"path": path, "old_text": old, "new_text": new})
            if not res.ok:
                print(f"!! shortcut edit #{index} on {path} refused: {res.error}")
                return 1
            steps.append({"idx": index, "thought": "", "tokens": {}, "latency_ms": 0,
                          "tool_calls": [{"id": "e%d" % index, "name": "edit_file",
                                          "args": {"path": path, "old_text": old, "new_text": new}}],
                          "observations": []})
        env.call_tool("run_tests", {})
        state = env.state()
    steps.append({"idx": 98, "thought": "", "tokens": {}, "latency_ms": 0,
                  "tool_calls": [{"id": "rt2", "name": "run_tests", "args": {}}], "observations": []})
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
