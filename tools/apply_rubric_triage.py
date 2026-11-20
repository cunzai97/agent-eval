"""按 2026-09-21 裁定收敛裁判的作用域：确定性的东西不再请 LLM 复核。

裁定原话：『模型要能够重试才行，然后裁判还是需要的，只不过确定性的东西就不需要裁判了』。

依据是实测的"闸门贡献"表（对 17 道带 rubric 的题，逐条比较"记录判定"与"只看确定性判据"）：

    task                 attempts  judged  翻转
    locate-bug-001..007      33-35   31-32    0
    locate-bug-006             34      31     1   <- 唯一一次：考的正是"读过几个文件"的宽度
    memory-loop-001..004     33-34   31-32    0
    data-pipeline-007/009       0       0     0   （新题，从未被评）
    debug-stacktrace-010        1       0     0
    script-dev-007              0       0     0

所以 345 次判词里裁判独立改变结论 1 次，而那 1 次考量的维度在 §10.9 已经被裁定为
"过程宽度不当能力代理"。据此：

- **删掉 rubric**（确定性判据已经逐条覆盖它索要的东西）：locate-bug-001~008、
  debug-stacktrace-010、script-dev-007。
- **保留 rubric 并钉死裁判档案**（`judge_id`，进 task_fingerprint）：data-pipeline-007/008/009、
  memory-loop-001~004。留下的唯一理由是**来源**问题——产物数字全对可能是脚本算的，
  也可能是硬编码或对 read_file 片段的小样本估算，终态看不出区别。等 N1（扰动输入后
  复算同一脚本）建成，这一档裁判也应删除。

    python3 tools/apply_rubric_triage.py            # 应用
    python3 tools/apply_rubric_triage.py --check    # 只校验当前状态

重复执行安全：已处理过的文件会被跳过。
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DROP = ["locate-bug-00%d" % i for i in range(1, 9)] + ["debug-stacktrace-010", "script-dev-007"]
KEEP = ["data-pipeline-007", "data-pipeline-008", "data-pipeline-009",
        "memory-loop-001", "memory-loop-002", "memory-loop-003", "memory-loop-004"]
JUDGE_ID = "luna-max:gpt-5.6-luna"

NOTE = [
    "# rubric 已删除（2026-09-21 裁定：确定性的东西不需要裁判）。",
    "# 这份细则原本索要的东西，下面的确定性判据已经逐条覆盖：答案键、必读文件、测试与 grep",
    "# 证据、产物精确数值。实测它作为闸门的总贡献是 345 次判词里翻转 1 次，而那 1 次翻转考的",
    "# 是『读过几个文件』的宽度——§10.9 已裁定过程宽度不当能力代理。",
]
KEEPNOTE = [
    "# 保留裁判的唯一理由：数字与记忆的**来源**无法从终态判定——产物数字全对，可能是脚本算",
    "# 出来的，也可能是硬编码或对 read_file 片段的小样本估算。judge_id 把尺钉死在这里：",
    "# 换裁判档案=换评分标准，实跑与它不符时该次判定记『未评』。",
    "# N1（扰动输入后复算同一脚本）建成之后，这一档裁判就该被它替换掉并删除。",
    "judge_id: %s" % JUDGE_ID,
]

RUBRIC_START = re.compile(r"^rubric:\s*[|>]")


def specs():
    out = {}
    for f in sorted(glob.glob(str(ROOT / "tasks" / "*" / "*.yaml"))):
        d = yaml.safe_load(open(f, encoding="utf-8"))
        out[d["id"]] = Path(f)
    return out


def block_span(lines, start):
    j = start + 1
    while j < len(lines) and (not lines[j].strip() or lines[j].startswith(("  ", "\t"))):
        j += 1
    return start, j


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告当前状态，不改文件")
    args = ap.parse_args()
    paths = specs()
    bad = 0
    for tid in DROP:
        p = paths[tid]
        text = p.read_text(encoding="utf-8")
        d = yaml.safe_load(text)
        has_rubric = bool(d.get("rubric"))
        if args.check:
            ok = not has_rubric and "rubric 已删除" in text
            print("  [%s] %-22s rubric=%s" % ("OK" if ok else "TODO", tid, has_rubric))
            bad += not ok
            continue
        if not has_rubric:
            print("  --   %-22s 已处理" % tid)
            continue
        lines = text.splitlines()
        idx = [i for i, ln in enumerate(lines) if RUBRIC_START.match(ln)]
        assert len(idx) == 1, (tid, idx)
        a, b = block_span(lines, idx[0])
        p.write_text("\n".join(lines[:a] + NOTE + lines[b:]) + "\n", encoding="utf-8")
        print("  DROP %-22s %-46s 删除 %d 行" % (tid, p.relative_to(ROOT), b - a))
    for tid in KEEP:
        p = paths[tid]
        text = p.read_text(encoding="utf-8")
        d = yaml.safe_load(text)
        if args.check:
            ok = bool(d.get("rubric")) and d.get("judge_id") == JUDGE_ID
            print("  [%s] %-22s rubric=%s judge_id=%s"
                  % ("OK" if ok else "TODO", tid, bool(d.get("rubric")), d.get("judge_id")))
            bad += not ok
            continue
        if d.get("judge_id") == JUDGE_ID:
            print("  --   %-22s 已有 judge_id" % tid)
            continue
        lines = text.splitlines()
        idx = [i for i, ln in enumerate(lines) if RUBRIC_START.match(ln)]
        assert len(idx) == 1, tid
        lines[idx[0]:idx[0]] = KEEPNOTE
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("  KEEP %-22s %-46s judge_id 已写入" % (tid, p.relative_to(ROOT)))
    # 全量自检：删完必须还能加载，且 rubric/judge_id 的组合合法
    for tid, p in specs().items():
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        if d.get("judge_id") and not d.get("rubric"):
            print("  !! %s 有 judge_id 却没有 rubric" % tid)
            bad += 1
    print("\n%s" % ("状态一致" if not bad else "仍有 %d 处不一致" % bad))
    return 1 if (args.check and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
