"""生成 50 题的现状清单：谁被改过、被怎么改的、谁没动。

    python3 tools/task_status.py            # 重写 docs/TASK_STATUS.md

参照系是基线提交 `ea176cb`（初始化 40 题套件）。每题按题面 diff 打标签，标签含义：

- `新建`            —— 基线之后加的题
- `加隐藏用例`      —— 题面新增 `state.hidden_tests` 判据（post-run 隐藏测试开始管这道题）
- `过程计数放宽`    —— §10.9：`min_calls` / `files_read >= N` / `grep_calls >= 2` / 三步 order_of 被降级
- `删 rubric` / `钉 judge_id` —— §10.11 裁判收敛
- `改判 medium`、`预算`、`题面/提示词`、`答案键` —— 其余单点改动

框架级改动（重试与"未评"、沙箱解释器 digest、清字节码、grep 目录语义、read_file 截断契约）
不体现在题面 diff 里，对全部 50 题同时生效，所以清单末尾单列一段。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BASE = "ea176cb"
OUT = ROOT / "docs" / "TASK_STATUS.md"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    base_paths = {}
    for p in git("ls-tree", "-r", "--name-only", BASE, "--", "tasks").split():
        if p.endswith(".yaml"):
            base_paths[os.path.basename(p)[:-5]] = p
    cur = {}
    for f in sorted(glob.glob(str(ROOT / "tasks" / "*" / "*.yaml"))):
        path = Path(f)
        cur[load(path)["id"]] = path

    # 隐藏判据的"内容"变更可以完全发生在题面之外：code_repo 看 hidden_tests/<快照>.py，
    # shell_ws 看 shell_hidden.py 里以快照名为 key 的那几张表。题面没动不等于判定没变严。
    hidden_code_changes: dict[str, bool] = {}
    shell_diff = git("diff", f"{BASE}..HEAD", "--", "evalkit/envs/shell_hidden.py")
    changed_files = set(git("diff", "--name-only", f"{BASE}..HEAD", "--",
                            "evalkit/envs/hidden_tests").split())
    for snap in {load(p)["environment"]["snapshot"] for p in cur.values()}:
        hit = any(snap in f for f in changed_files)
        if not hit and any(l.startswith(("+", "-")) and snap in l for l in shell_diff.splitlines()):
            hit = True
        hidden_code_changes[snap] = hit

    def load_from_git(p):
        return yaml.safe_load(git("show", f"{BASE}:{p}"))

    rows = []
    for tid, path in sorted(cur.items()):
        spec = load(path)
        new = tid not in base_paths
        old = None if new else load_from_git(base_paths[tid])
        tags = []
        if new:
            tags.append("新建")
        else:
            if (old.get("agent") or {}).get("system_prompt") != spec["agent"]["system_prompt"]:
                tags.append("提示词改")
            if old.get("task_prompt") != spec.get("task_prompt"):
                tags.append("题面改")
            if old.get("difficulty") != spec.get("difficulty"):
                tags.append("改判 %s" % spec["difficulty"])
            if (old.get("budget") or {}) != (spec.get("budget") or {}):
                tags.append("预算")
            if old.get("rubric") and not spec.get("rubric"):
                tags.append("删 rubric")
            if spec.get("judge_id") and not old.get("judge_id"):
                tags.append("钉 judge_id")

            def keys(spec_):
                return {json.dumps(c, ensure_ascii=False, sort_keys=True)
                        for c in (spec_.get("success") or [])}
            added_c, gone_c = keys(spec) - keys(old), keys(old) - keys(spec)
            old_had_hidden = any("hidden_tests" in json.dumps(c, ensure_ascii=False)
                                 for c in (old.get("success") or []))
            if any("hidden_tests" in c for c in added_c) and not old_had_hidden:
                tags.append("加隐藏用例(shell_hidden)" if spec["environment"]["name"] == "shell_ws"
                            else "加隐藏用例")
            elif any("hidden_tests" in c for c in added_c | gone_c):
                tags.append("隐藏用例判据改")
            if any(("min_calls" in c or "files_read" in c or "order_of" in c
                    or "grep_calls >= 2" in c) for c in gone_c):
                tags.append("过程计数放宽")
            if any("answer_json" in c for c in added_c | gone_c):
                tags.append("答案键改")
            other_up = {c for c in added_c if "hidden_tests" not in c and "answer_json" not in c}
            other_dn = {c for c in gone_c if not ("min_calls" in c or "files_read" in c
                                                  or "order_of" in c or "grep_calls >= 2" in c
                                                  or "answer_json" in c)}
            if other_up:
                tags.append("判据 +%d" % len(other_up))
            if other_dn:
                tags.append("判据 -%d" % len(other_dn))
            if not tags:
                tags.append("题面未动·隐藏判据已变"
                            if hidden_code_changes.get(spec["environment"]["snapshot"]) else "未动")
        rows.append((tid, spec["category"], spec["difficulty"], "、".join(dict.fromkeys(tags))))

    counts = Counter(t for r in rows for t in r[3].split("、"))
    untouched = [r[0] for r in rows if r[3] == "未动"]
    hidden_only = [r[0] for r in rows if r[3] == "题面未动·隐藏判据已变"]
    added = [r[0] for r in rows if "新建" in r[3]]
    hidden_new = [r[0] for r in rows if any(tg.startswith("加隐藏用例") for tg in r[3].split("、"))]
    under_hidden = sorted(t for t, p in cur.items()
                          if any("hidden_tests" in str(c.get("expr", ""))
                                 for c in load(p).get("success", [])))
    def _hid(spec):
        return any("hidden_tests" in str(c.get("expr", "")) for c in (spec.get("success") or []))
    base_had = sorted(t for t in base_paths if t in cur and _hid(load_from_git(base_paths[t])))
    new_with_hidden = sorted(t for t in cur if t not in base_paths and _hid(load(cur[t])))
    rubric_kept = sorted(t for t, p in cur.items() if load(p).get("rubric"))

    out = [
        "# 50 题现状：谁被改过、被怎么改的、谁没动",
        "",
        "本文件由 `python3 tools/task_status.py` 生成，参照系是基线提交 `%s`（初始化 40 题）。" % BASE,
        "",
        "- **新建 %d 道**：%s" % (len(added), "、".join(added)),
        "- **题面一字未动 %d 道**：%s（快照与隐藏判据同样未动）" % (len(untouched), "、".join(untouched) or "无"),
        "- 其余 **%d 道**题面被改过。判定加严的主线是 post-run 隐藏用例：**现在 %d/50 道题归它管，"
        "基线只有 %d/40**——其中 %d 道是老题新加上来的（%s），%d 道基线就有、只是用例内容被重做，"
        "%d 道新建时就自带（%s）。" % (
            len(rows) - len(added) - len(untouched) - len(hidden_only), len(under_hidden), len(base_had),
            len(hidden_new), "、".join(sorted(hidden_new)),
            len([t for t in base_had if t not in hidden_new]),
            len(new_with_hidden), "、".join(new_with_hidden)),
        "- 裁判现在只管 %d 道题：%s" % (len(rubric_kept), "、".join(rubric_kept)),
        "",
        "| 题 | 类别 | 难度 | 相对基线的改动 |",
        "|---|---|---|---|",
    ]
    out += ["| %s | %s | %s | %s |" % r for r in rows]
    out += [
        "",
        "标签计数：%s" % "、".join("%s %d" % (k, v) for k, v in counts.most_common()),
        "",
        "## 三处框架级改动不在题面 diff 里，但对 50 题全部生效",
        "",
        "1. **重试与「未评」**（`fc7b8fa`）：上游连不上/5xx/断连/读超时按指数退避重试，且重试不吃本题",
        "   剩余墙钟；用尽后 `termination=infra_error` → `graded=False` → 不进通过率分母、单独计数。",
        "   跑批前探上游，不可达直接返回 2 不建 run。",
        "2. **过程计数降级**（§10.9）：`min_calls`、`files_read >= N`、`grep_calls >= 2`、三步 `order_of`",
        "   不再当能力代理；防假对整体交给隐藏用例与钉死的产物数值。",
        "3. **沙箱与工具语义**：解释器从浮动标签改成 digest 钉定、铺环境即清字节码、`grep` 打目录不再",
        "   静默返回空、`read_file` 明确 12,000 字符截断契约——这几条改的是环境层，题面看不出来。",
        "",
        "## 两个要知道的耦合",
        "",
        "- `locate-bug-001` 与 `fix-failing-test-001` 共用 `calc_buggy` 快照：给后者加 32 条隐藏用例时，",
        "  前者的判定也一起变严（它不读 `state.hidden_tests`，结论不变，代价是收尾多跑 0.4 秒、指纹换掉）。",
        "- `debug-stacktrace-006` 的题面已经引用 `state.hidden_tests`，而 `hidden_tests/debug-006.py` 目前",
        "  **未入库**（另一会话在改这题）。那份文件一旦丢了，这道题会变成永远判不对——本轮审计跑成 50/50",
        "  只是因为文件此刻在本地。这是别人手上的活，但依赖要记下。",
        "",
    ]
    OUT.write_text("\n".join(out), encoding="utf-8")
    print("写出 %s（%d 题；未动 %d；新建 %d；隐藏用例覆盖 %d；裁判 %d）"
          % (OUT.relative_to(ROOT), len(rows), len(untouched), len(added), len(hidden_new), len(rubric_kept)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
