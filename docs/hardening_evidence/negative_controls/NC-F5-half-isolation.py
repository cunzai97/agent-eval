"""Negative control for fix-failing-test-005 (snapshot fix-005): 半边隔离的深合并.

症状修得很像样：优先级顺序改对了（defaults < file_config < env_config），递归也真的
递归了，可见的 9 条测试因此全绿。但隔离只做了一半——`base` 侧的值 deepcopy 了，
`override` 侧的值原样交引用。可见套件那条 aliasing 探针写的是从 **base** 经过 merged
键拿到的那个 list，所以它照样绿；而"结果不得与任何一层输入共享可变子对象"这条契约
在 override 侧、在第三层以上、在 list-of-dict 这类形状上全都还活着。

    python3 docs/hardening_evidence/negative_controls/NC-F5-half-isolation.py

期望：可见 9/9 绿、题面三条原有 state_assert 全过、隐藏 <total。
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

MERGE_OLD = (
    "def merge(base, override):\n"
    "    result = dict(override)\n"
    "    result.update(base)\n"
    "    return result\n"
)
MERGE_SHORTCUT = (
    "import copy\n"
    "\n"
    "\n"
    "def merge(base, override):\n"
    "    result = {}\n"
    "    for key, value in base.items():\n"
    "        result[key] = copy.deepcopy(value)\n"
    "    for key, value in override.items():\n"
    "        existing = base.get(key)\n"
    "        if isinstance(existing, dict) and isinstance(value, dict):\n"
    "            result[key] = merge(existing, value)\n"
    "        else:\n"
    "            result[key] = value          # 捷径：override 侧原样交引用\n"
    "    return result\n"
)
RESOLVE_OLD = "    return merge(env_config, merge(file_config, defaults))"
RESOLVE_NEW = "    return merge(merge(defaults, file_config), env_config)"


def main() -> int:
    spec = TaskSpec(**yaml.safe_load(
        open(ROOT / "tasks/fix-and-test/fix-failing-test-005.yaml", encoding="utf-8")))
    steps = [{"idx": -1, "thought": "", "tokens": {}, "latency_ms": 0,
              "tool_calls": [{"id": "rt1", "name": "run_tests", "args": {}}], "observations": []}]
    with tempfile.TemporaryDirectory(prefix="ncf5-") as tmp:
        env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                      work_dir=tmp, base=str(ROOT / "environments"))
        env.call_tool("run_tests", {})
        for index, (path, old, new) in enumerate(
                [("cfg/merge.py", MERGE_OLD, MERGE_SHORTCUT),
                 ("cfg/resolve.py", RESOLVE_OLD, RESOLVE_NEW)], 1):
            res = env.call_tool("edit_file", {"path": path, "old_text": old, "new_text": new})
            if not res.ok:
                print(f"!! 捷径编辑第 {index} 步被拒：{path} — {res.error}")
                return 1
            steps.append({"idx": index, "thought": "", "tokens": {}, "latency_ms": 0,
                          "tool_calls": [{"id": f"e{index}", "name": "edit_file",
                                          "args": {"path": path, "old_text": old, "new_text": new}}],
                          "observations": []})
        env.call_tool("run_tests", {})
        state = env.state()
    steps.append({"idx": 98, "thought": "", "tokens": {}, "latency_ms": 0,
                  "tool_calls": [{"id": "rt2", "name": "run_tests", "args": {}}], "observations": []})
    steps.append({"idx": 99, "thought": "", "tokens": {}, "latency_ms": 0,
                  "tool_calls": [{"id": "d", "name": "done", "args": {}}], "observations": []})

    report = state.get("test_report") or {}
    print("visible pytest:", {k: report.get(k) for k in ("passed", "failed", "errors")})
    hidden = state.get("hidden_tests") or {}
    print(f"hidden result : {hidden.get('passed')}/{hidden.get('total')} applicable={hidden.get('applicable')}")
    checks = run_checks(spec, Trajectory(**{"task_id": spec.id, "model": "nc", "seed": 0,
                                            "started_at": "x", "termination": "done",
                                            "total_steps": 3, "total_tokens": 0,
                                            "elapsed_s": 0.0, "steps": steps}), state)
    for c in checks:
        print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.detail[:88]}")
    visible_green = report.get("failed") == 0 and report.get("errors") == 0
    hidden_red = hidden.get("applicable") and hidden.get("passed") < hidden.get("total")
    pre_existing = all(c.passed for c in checks if "hidden_tests" not in c.detail)
    print("VERDICT:", "可见绿 + 隐藏红 -> 闸门覆盖了这个空档"
          if (visible_green and hidden_red) else
          f"不合格（visible_green={visible_green} hidden_red={hidden_red} 其余判据={pre_existing}）")
    return 0 if (visible_green and hidden_red) else 1


if __name__ == "__main__":
    raise SystemExit(main())
