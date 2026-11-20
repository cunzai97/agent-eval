"""把一条轨迹压成人能读的解题过程，并自动标出常见失败形态。

    python3 tools/read_traj.py runs/<run>/<model>/<task>
    python3 tools/read_traj.py runs/<run>/<model>/<task> --steps 40 --show-obs 300
    python3 tools/read_traj.py --last live-qwen1235-t1        # 该 run 下所有题

关注点不是分数，是过程：模型有没有按题面理解任务、卡在哪一步、
失败是它自己的问题还是题目把路走死了。自动标记：
  REPEAT   同工具同参数重复调用（含失败后重复）
  ERR      工具返回 ok=False
  NO_EVID  从未跑过测试/未读相关文件就下结论
  BUDGET   步数或 token 逼近题面上限
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(d: str) -> dict | None:
    tj = os.path.join(d, "trajectory.json")
    if not os.path.isfile(tj):
        return None
    tr = json.loads(Path(tj).read_text(encoding="utf-8"))
    rj = os.path.join(d, "result.json")
    tr["_result"] = json.loads(Path(rj).read_text(encoding="utf-8")) if os.path.isfile(rj) else {}
    sj = os.path.join(d, "state.json")
    tr["_state"] = json.loads(Path(sj).read_text(encoding="utf-8")) if os.path.isfile(sj) else {}
    return tr


def summarise(tr: dict, show_obs: int) -> list[str]:
    out = []
    calls: Counter = Counter()
    for step in tr.get("steps", []):
        for tc, obs in zip(step.get("tool_calls", []), step.get("observations", [])):
            name = tc.get("name")
            args = tc.get("args") or {}
            key = (name, json.dumps(args, ensure_ascii=False, sort_keys=True)[:160])
            calls[key] += 1
            flags = []
            if calls[key] > 1:
                flags.append(f"REPEAT x{calls[key]}")
            if not obs.get("ok", True):
                flags.append("ERR")
            arg_txt = json.dumps(args, ensure_ascii=False)
            if len(arg_txt) > 120:
                arg_txt = arg_txt[:117] + "..."
            line = f"  {step.get('idx', '?'):>3} {name}({arg_txt})"
            if flags:
                line += "   [" + ",".join(flags) + "]"
            out.append(line)
            body = obs.get("result") if obs.get("ok", True) else obs.get("error")
            txt = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            txt = (txt or "")[:show_obs].replace("\n", " ⏎ ")
            if txt:
                out.append(f"       -> {txt}")
        th = (step.get("thought") or "").strip().replace("\n", " ")
        if th:
            out.append(f"       thought: {th[:200]}")
    return out


def diagnose(tr: dict) -> list[str]:
    notes = []
    res = tr.get("_result") or {}
    checks = res.get("checks") or []
    failed = [c for c in checks if not c.get("passed")]
    if res:
        notes.append(f"passed={res.get('passed')} 判定失败项 {len(failed)}/{len(checks)}"
                     + (f"  termination={tr.get('termination')}" if tr.get("termination") else ""))
    for c in failed[:6]:
        notes.append(f"  FAIL {c.get('type')}: {str(c.get('detail'))[:110]}"
                     + (f"  | {str(c.get('evidence'))[:90]}" if c.get("evidence") else ""))
    seq = [tc.get("name") for s in tr.get("steps", []) for tc in s.get("tool_calls", [])]
    pair = Counter(zip(seq, seq[1:]))
    for (a, b), n in pair.most_common(3):
        if a == b and n >= 2:
            notes.append(f"  LOOP? 连续同工具 {a} -> {b} 出现 {n} 次")
    ran_tests = sum(1 for x in seq if x in {"run_tests", "run_python", "run_bash"})
    answered = sum(1 for x in seq if x == "answer")
    if answered and not ran_tests:
        notes.append("  NO_EVID 未运行任何测试/脚本就提交答案")
    b = res.get("budget") or {}
    eff = res.get("efficiency") or {}
    if eff and b:
        for k_e, k_b in (("steps", "max_steps"), ("tokens", "max_tokens"), ("elapsed_s", "timeout_s")):
            if eff.get(k_e) and b.get(k_b) and eff[k_e] / b[k_b] > 0.85:
                notes.append(f"  BUDGET {k_e} 用掉 {eff[k_e]}/{b[k_b]}")
    return notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", help="runs/<run>/<model>/<task> 目录")
    ap.add_argument("--last", help="跑某个 run 目录下全部题（传 run 名或路径）")
    ap.add_argument("--steps", type=int, default=99, help="最多打印多少步")
    ap.add_argument("--show-obs", type=int, default=160, help="每步观测打印字符数")
    ap.add_argument("--quiet", action="store_true", help="只打印诊断，不打印过程")
    a = ap.parse_args()

    dirs = []
    if a.last:
        run = a.last if os.path.isdir(a.last) else str(ROOT / "runs" / a.last)
        dirs = sorted(os.path.dirname(p) for p in glob.glob(run + "/*/*/result.json"))
    elif a.dir:
        dirs = [a.dir]
    else:
        ap.error("需要 dir 或 --last")

    for d in dirs:
        tr = load(d)
        if not tr:
            continue
        task = tr.get("task_id") or os.path.basename(d)
        eff = tr.get("_result", {}).get("efficiency", {})
        print(f"\n=== {task} | {tr.get('model','?')[:34]} | steps={tr.get('total_steps')} "
              f"tokens={eff.get('tokens')} elapsed={eff.get('elapsed_s')}s term={tr.get('termination')}")
        for n in diagnose(tr):
            print(n)
        if not a.quiet:
            body = summarise(tr, a.show_obs)
            for line in body[:a.steps * 3]:
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
