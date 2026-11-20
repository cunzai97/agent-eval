#!/usr/bin/env python3
"""从 runs/_HISTORY.jsonl 聚合历史通过率，选最难的一批题组成题库写入 banks/。

规则：按历史整体通过率升序取前 N；历史全对(零失败)的题自然落在最后被排除。
用法：python3 tools/build_bank.py --n 40 --name hard40
"""
import argparse, collections, glob, json, os, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def task_ids():
    ids = []
    for p in sorted(glob.glob(os.path.join(ROOT, "tasks", "**", "*.yaml"), recursive=True)):
        for l in open(p):
            if l.startswith("id:"):
                ids.append(l.split(":", 1)[1].strip()); break
    return ids

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--name", default="hard40")
    args = ap.parse_args()

    stats = collections.defaultdict(lambda: {"att": 0, "pass": 0})
    nruns = 0
    with open(os.path.join(ROOT, "runs", "_HISTORY.jsonl")) as f:
        for ln in f:
            try:
                rec = json.loads(ln.strip())
            except Exception:
                continue
            nruns += 1
            for mrec in (rec.get("models") or {}).values():
                for tid, tres in (mrec.get("tasks") or {}).items():
                    stats[tid]["att"] += 1
                    stats[tid]["pass"] += 1 if tres.get("passed") else 0

    rows = []
    for tid in task_ids():
        s = stats.get(tid, {"att": 0, "pass": 0})
        r = s["pass"] / s["att"] if s["att"] else None
        rows.append((r, tid, s))
    # 通过率升序；无记录视为最难排前（新题）
    rows.sort(key=lambda x: (x[0] is None, x[0] if x[0] is not None else 0.0))
    picked = rows[: args.n]
    lines = [
        f"name: {args.name}",
        f"description: 按历史整体通过率升序取前{args.n}题（含全部新开发未测难题）；"
        f"历史零失败的题被排除。生成于 {datetime.date.today()}",
        "source: runs/_HISTORY.jsonl",
        f"n_runs: {nruns}",
        "tasks:",
    ]
    detail = ["detail:"]
    for r, tid, s in picked:
        lines.append(f"  - {tid}")
        pct = "new/untested" if r is None else f"{r*100:.1f}% ({s['pass']}/{s['att']})"
        detail.append(f"  {tid}: {pct}")
    outdir = os.path.join(ROOT, "banks")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{args.name}.yaml")
    open(path, "w").write("\n".join(lines + detail) + "\n")
    cut = picked[-1]
    print(f"写入 {path}: {len(picked)} 题，切线题 {cut[1]} = "
          f"{'untested' if cut[0] is None else format(cut[0]*100,'.1f')+'%'}")

if __name__ == "__main__":
    main()
