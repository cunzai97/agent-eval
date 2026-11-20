#!/usr/bin/env python3
"""对比两个 run（或两组 run）在**相同题目**上的效率：步数 / token / 耗时 / 通过。

用法：
  python3 tools/compare_runs.py runs/web-20261010_205756_992 runs/web-20261009_002817_127
  python3 tools/compare_runs.py RUN_A RUN_B --labels swift 原版
只统计两边都跑过（有 result.json）的题；每题取 result.json 的 efficiency 字段。
"""
import argparse, glob, json, os, statistics

def load_run(run_dir):
    out = {}
    for model_dir in sorted(glob.glob(os.path.join(run_dir, "*"))):
        if not os.path.isdir(model_dir) or os.path.basename(model_dir).startswith("."):
            continue
        for task_dir in sorted(glob.glob(os.path.join(model_dir, "*"))):
            rp = os.path.join(task_dir, "result.json")
            if not os.path.isfile(rp):
                continue
            try:
                r = json.load(open(rp, encoding="utf-8"))
            except Exception:
                continue
            e = r.get("efficiency") or {}
            out[r.get("task_id") or os.path.basename(task_dir)] = {
                "passed": bool(r.get("passed")), "steps": e.get("steps") or 0,
                "tokens": e.get("tokens") or 0, "sec": e.get("elapsed_s") or 0.0,
                "model": r.get("model"),
            }
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_a"); ap.add_argument("run_b")
    ap.add_argument("--labels", nargs=2, default=["A", "B"])
    ap.add_argument("--ratio-breakdown", action="store_true",
                    help="额外输出 token-per-step（单步成本）对比")
    args = ap.parse_args()

    A, B = load_run(args.run_a), load_run(args.run_b)
    la, lb = args.labels
    common = sorted(set(A) & set(B))
    if not common:
        print("没有可比题（两边都跑过的题为空）"); return
    m_a = next((v["model"] for v in A.values()), la)
    m_b = next((v["model"] for v in B.values()), lb)
    print(f"{la}: {m_a}  ({len(A)} 题已完成)")
    print(f"{lb}: {m_b}  ({len(B)} 题已完成)")
    print(f"可比交集: {len(common)} 题\n")
    hdr = f"{'task':28}{la+' 步/token/秒':>30}{lb+' 步/token/秒':>30}  通过"
    print(hdr); print("-" * len(hdr))
    for t in common:
        a, b = A[t], B[t]
        flag = ""
        if a["passed"] != b["passed"]:
            flag = f"  ←{la}过/{lb}挂" if a["passed"] else f"  ←{la}挂/{lb}过"
        print(f"{t:28}{a['steps']:>9}/{a['tokens']:>8}/{a['sec']:>9.0f}"
              f"{b['steps']:>9}/{b['tokens']:>8}/{b['sec']:>9.0f}"
              f"   {int(a['passed'])}/{int(b['passed'])}{flag}")
    print("-" * len(hdr))
    n = len(common)
    def agg(run, key):
        vals = [run[t][key] for t in common]
        return sum(vals) / n, statistics.median(vals)
    for name, run in ((la, A), (lb, B)):
        sm, md = agg(run, "steps"); tm, _ = agg(run, "tokens"); em, _ = agg(run, "sec")
        passed = sum(1 for t in common if run[t]["passed"])
        print(f"{name:6} 均值: 步 {sm:5.1f} | token {tm:6.0f} | 秒 {em:6.1f} | 通过 {passed}/{n}"
              f"   ‖ 中位: 步 {md:.0f} | token {statistics.median([run[t]['tokens'] for t in common]):.0f}"
              f" | 秒 {statistics.median([run[t]['sec'] for t in common]):.0f}")
    ds, do = agg(A, "steps")[0], agg(B, "steps")[0]
    ts, to = agg(A, "tokens")[0], agg(B, "tokens")[0]
    es, eo = agg(A, "sec")[0], agg(B, "sec")[0]
    print(f"\n{la} 相对 {lb}: 步数 {ds-do:+.1f} | token {ts-to:+.0f} ({(ts/to-1)*100:+.1f}%)"
          f" | 秒 {es-eo:+.1f} ({(es/eo-1)*100:+.1f}%)"
          f" | 通过 {sum(1 for t in common if A[t]['passed'])} vs {sum(1 for t in common if B[t]['passed'])}")
    if args.ratio_breakdown:
        ra = ts / max(ds, 0.001); rb = to / max(do, 0.001)
        print(f"单步 token: {la} {ra:.0f} vs {lb} {rb:.0f} ({ra-rb:+.0f})")

if __name__ == "__main__":
    main()
