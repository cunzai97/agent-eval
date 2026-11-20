"""历史轨迹回放闸门：把 run 过的轨迹灌进（可能已加难的）题面，看重算的判定。

用途：给题加难（尤其加隐藏测试）后，必须知道"以前做对过的尝试还剩多少"。
 - 全死 => 闸门过锐，正常解法也被误杀；
 - 几乎全活 => 新闸门没测到真东西。

用法：
    python3 tools/replay_gate.py debug-stacktrace-003
    python3 tools/replay_gate.py fix-failing-test-004 --spec /tmp/probe.yaml
    python3 tools/replay_gate.py debug-stacktrace-006 --only-passed

注意：--spec 指向的 YAML 的 id 必须与回放轨迹的 task_id 一致，否则 snapshot 会取错。
写探针文件时保留原 id，只改 success 断言。
"""
from __future__ import annotations

import argparse
import json
import glob
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml                                             # noqa: E402
from evalkit.schema import TaskSpec, Trajectory         # noqa: E402
from evalkit.verifier import run_checks                 # noqa: E402
from evalkit.envs import get_env                        # noqa: E402


def load_spec(task_id: str, spec_path: str | None) -> TaskSpec:
    if spec_path:
        return TaskSpec(**yaml.safe_load(Path(spec_path).read_text(encoding="utf-8")))
    hits = glob.glob(f"tasks/**/{task_id}.yaml", recursive=True)
    if not hits:
        raise SystemExit(f"no task file for {task_id}")
    return TaskSpec(**yaml.safe_load(Path(hits[0]).read_text(encoding="utf-8")))


def replay(env, tr: dict) -> dict:
    for step in tr.get("steps", []):
        for tc in step.get("tool_calls", []):
            env.call_tool(tc.get("name"), tc.get("args") or {})
    return env.state()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--spec", help="替代题面（探针）；id 需与 task_id 相同")
    ap.add_argument("--only-passed", action="store_true", help="只回放历史 result.json 判对的轨迹")
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    spec = load_spec(a.task_id, a.spec)
    rows = []
    for rj in sorted(glob.glob(f"{a.runs}/*/*/{a.task_id}/result.json")):
        d = Path(rj).parent
        tj = d / "trajectory.json"
        if not tj.is_file():
            continue
        record = json.loads(Path(rj).read_text(encoding="utf-8"))
        if a.only_passed and not record.get("passed"):
            continue
        tr = json.loads(tj.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="agent-eval-replay-") as tmp:
            try:
                env = get_env(spec.environment.name,
                              snapshot=spec.environment.snapshot,
                              work_dir=tmp, base="environments")
                state = replay(env, tr)
            except Exception as ex:  # noqa: BLE001
                print(f"  !! replay failed for {d}: {ex}")
                continue
        checks = run_checks(spec, Trajectory(**tr), state)
        det = all(c.passed for c in checks) if checks else False
        hidden = (state or {}).get("hidden_tests") or {}
        rows.append({
            "model": tr.get("model") or d.parts[-2],
            "was_passed": bool(record.get("passed")),
            "now_passed": det,
            "visible": (state or {}).get("test_report", {}).get("passed"),
            "hidden": f"{hidden.get('passed')}/{hidden.get('total')}" if hidden.get("applicable") else "-",
            "failed_checks": [c.detail for c in checks if not c.passed],
        })

    n = len(rows)
    print(f"题面 {a.task_id} | 快照 {spec.environment.snapshot} | 轨迹 {n}")
    if not n:
        print("  无历史轨迹 —— 无法校准，改用人工参照解 + 负控")
        return 1
    was = [r for r in rows if r["was_passed"]]
    survivors = [r for r in was if r["now_passed"]]
    print(f"  历史判对 {len(was)} -> 现在仍判对 {len(survivors)}"
          + (f"  ({100*len(survivors)/len(was):.0f}%)" if was else ""))
    print(f"  全部 {n} 条现在判对 {sum(1 for r in rows if r['now_passed'])} ({100*sum(1 for r in rows if r['now_passed'])/n:.0f}%)")
    if not a.quiet:
        killed = Counter()
        for r in rows:
            if r["was_passed"] and not r["now_passed"]:
                for c in r["failed_checks"]:
                    killed[c[:60]] += 1
        for c, k in killed.most_common(8):
            print(f"    误杀 {k:>3}x  {c}")
    for r in rows:
        mark = "OK  " if r["now_passed"] else ("KILL" if r["was_passed"] else "    ")
        print(f"  [{mark}] was={r['was_passed']} now={r['now_passed']} visible={r['visible']} hidden={r['hidden']}  {r['model'][:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
