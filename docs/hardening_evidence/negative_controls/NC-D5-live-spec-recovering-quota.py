"""Negative control for debug-stacktrace-005 (snapshot debug-005): the shortcut the
visible pytest suite cannot see.

两处"看着像修好了"的写法，合起来让可见 8 条全绿：

1. ``funcs/make.py`` 用默认参数把循环变量绑进闭包——这正是晚绑定的教科书修法，但它绑的是
   **调用方那个 dict 对象**（``spec=spec``），不是配置的值。可见测试只在构造之后比对
   ``specs == [...]``，从没在构造之后动过 spec，于是"handler 捕获自己的配置"这条Promise
   依然没满足：调用方改一下 offset / limit / name，已建好的 handler 立刻跟着变。
2. ``funcs/state.py`` 的限额一旦超限就把这个 name 从计数表里抹掉（"下一批还能用"）。
   可见套件每条测试都只让 handler 超限一次，之后要么换 handler 要么换工厂，所以它看不出
   限额已经被这条 pop 悄悄重置；题面要的"限额"于是变成"每 N 次之后报错一次"。

工厂侧仍然给了独立的状态字典，所以 test_new_factory_has_fresh_state 与
test_factories_with_same_name_are_isolated 都还是绿的——这正是这份反例值得存在的原因：
它把可见套件能看到的两条都修了，剩下的两条缺陷只有隐藏用例能看见。

编辑打在临时副本上，tests/ 一个字都没动，两份源文件都算"改过"。

    python3 docs/hardening_evidence/negative_controls/NC-D5-live-spec-recovering-quota.py

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
    ("funcs/state.py",
     "COUNTS = {}\n\n\ndef consume(name, limit):\n"
     "    COUNTS[name] = COUNTS.get(name, 0) + 1\n"
     "    if COUNTS[name] > limit:\n"
     "        raise RuntimeError(\"call limit exceeded\")",
     "COUNTS = {}    # kept for any caller that still wants the module-level store\n\n\n"
     "def consume(counts, name, limit):\n"
     "    counts[name] = counts.get(name, 0) + 1\n"
     "    if counts[name] > limit:\n"
     "        # The quota is a rolling window: drop the record so the handler can\n"
     "        # be used again from scratch on the next call.\n"
     "        counts.pop(name, None)\n"
     "        raise RuntimeError(\"call limit exceeded\")"),
    ("funcs/make.py",
     "from .state import consume\n\n\ndef make_processors(specs):\n"
     "    processors = {}\n"
     "    for spec in specs:\n"
     "        name = spec[\"name\"]\n\n"
     "        def process(value):\n"
     "            consume(name, spec[\"limit\"])\n"
     "            return value + spec[\"offset\"]\n\n"
     "        processors[name] = process\n"
     "    return processors",
     "from .state import consume\n\n\n"
     "def make_processors(specs):\n"
     "    processors = {}\n"
     "    counts = {}                # one store per factory call -> fresh state\n\n"
     "    for spec in specs:\n"
     "        name = spec[\"name\"]\n"
     "        if name in processors:\n"
     "            raise ValueError(f\"duplicate handler name: {name!r}\")\n"
     "        if spec[\"limit\"] <= 0:\n"
     "            raise ValueError(\"limit must be positive\")\n\n"
     "        # Bind the loop variable as a default argument so each handler keeps\n"
     "        # its own configuration instead of the last spec seen.\n"
     "        def process(value, spec=spec, counts=counts):\n"
     "            consume(counts, spec[\"name\"], spec[\"limit\"])\n"
     "            return value + spec[\"offset\"]\n\n"
     "        processors[name] = process\n"
     "    return processors"),
]


def main() -> int:
    spec = TaskSpec(**yaml.safe_load(
        open(ROOT / "tasks/debug/debug-stacktrace-005.yaml", encoding="utf-8")))
    steps = [{"idx": -1, "thought": "", "tokens": {}, "latency_ms": 0,
              "tool_calls": [{"id": "rt1", "name": "run_tests", "args": {}}],
              "observations": []}]
    with tempfile.TemporaryDirectory(prefix="nc-d5-") as tmp:
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
