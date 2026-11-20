#!/usr/bin/env python3
"""按题库统计各模型总分排名。

数据源：runs/_HISTORY.jsonl（按 timestamp 顺序，同题后跑覆盖先跑）。
用法：python3 tools/bank_board.py [--bank hard40] [--model luna-6]
"""
import argparse, collections, json, os, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def bank_tasks(name):
    p = os.path.join(ROOT, "banks", f"{name}.yaml")
    ids = []
    for l in open(p, encoding="utf-8"):
        if l.startswith("  - "):
            ids.append(l.strip()[2:])
    return ids

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default="hard40")
    ap.add_argument("--model", default=None, help="重点标出这个模型的明细")
    ap.add_argument("--min-ran", type=int, default=20,
                    help="只显示跑过至少这么多题的模型（默认 20，滤掉 smoke/demo 标签）")
    ap.add_argument("--alias", action="append", default=[],
                    help="把某个 run 标签并进另一个模型名，如 luna6-new12=luna-6（可多次）")
    args = ap.parse_args()

    alias = {}
    for pair in args.alias:
        if "=" in pair:
            old, new = pair.split("=", 1)
            alias[old.strip()] = new.strip()
    alias_keys = sorted({len(k) for k in alias}, reverse=True)

    def canon(model):
        for k in alias_keys:
            if model[:k] in alias:
                return alias[model[:k]]
        return model

    ids = bank_tasks(args.bank)
    idset = set(ids)
    # model -> task -> (passed, ts)
    scores = collections.defaultdict(dict)
    ts = {}
    for ln in open(os.path.join(ROOT, "runs", "_HISTORY.jsonl")):
        try:
            rec = json.loads(ln.strip())
        except Exception:
            continue
        when = rec.get("timestamp") or rec.get("run_id") or ""
        for model, mrec in (rec.get("models") or {}).items():
            model = canon(model)
            for tid, tres in (mrec.get("tasks") or {}).items():
                if tid not in idset:
                    continue
                prev = scores[model].get(tid)
                if prev is None or when >= ts.get((model, tid), ""):
                    scores[model][tid] = bool(tres.get("passed"))
                    ts[(model, tid)] = when

    rows = []
    for model, m in scores.items():
        ran = len(m)
        if ran < args.min_ran:
            continue
        passed = sum(1 for v in m.values() if v)
        rows.append((passed, ran, model, m))
    rows.sort(key=lambda x: (-x[0], -x[1], x[2]))

    print(f"== {args.bank} 总分排名（题库 {len(ids)} 题；只统计该模型跑过的题）==")
    print(f"{'名次':<4}{'模型':<28}{'通过':>5}{'已跑':>6}{'通过率':>9}")
    for i, (passed, ran, model, _m) in enumerate(rows, 1):
        rate = f"{passed/ran*100:.1f}%" if ran else "-"
        star = "  ←" if args.model and model == args.model else ""
        print(f"{i:<4}{model:<28}{passed:>5}{ran:>6}{rate:>9}{star}")

    if args.model and args.model in scores:
        m = scores[args.model]
        miss = [t for t in ids if t not in m]
        fail = [t for t in ids if m.get(t) is False]
        print(f"\n[{args.model}] 未跑 {len(miss)} 题" + (f": {', '.join(miss)}" if miss else ""))
        print(f"[{args.model}] 挂掉 {len(fail)} 题" + (f": {', '.join(fail)}" if fail else ""))

    # 共同子集：所有模型都跑过的题上再排一次（题数不同时更公平）
    if rows:
        common = set.intersection(*(set(m.keys()) for _p, _r, _mo, m in rows)) if rows else set()
        if common:
            sub = []
            for _p, _r, model, m in rows:
                sub.append((sum(1 for t in common if m[t]), model))
            sub.sort(key=lambda x: (-x[0], x[1]))
            n = len(common)
            print(f"\n== 共同子集榜（{n} 题：全部模型都跑过的题）==")
            for i, (passed, model) in enumerate(sub, 1):
                star = "  ←" if args.model and model == args.model else ""
                print(f"{i:<4}{model:<28}{passed:>5}/{n}{passed/n*100:>8.1f}%{star}")

if __name__ == "__main__":
    main()
