"""Negative control for fix-failing-test-003 (snapshot fix-003): 分隔符只认可见套件演示过的那几个.

规范化层这次真修好了（NFKD + casefold + 去组合音标 + TypeError），撇号删了，长度也按
整词退让，可见 8 条因此全绿。留着的是原实现那一行 `re.sub(r"[^a-z0-9-]", "", value)`
的口径：标点被*删掉*而不是当分隔符。可见套件里出现过的分隔符只有空格、下划线、连字符
三种，所以把这三个各自换成 '-' 就表面上满足了"合并分隔符"；而 "a.b"->"ab"、
"a\\tb"->"ab" 让两个不同标题落成同一个 slug——可见测试从没有把标点放在两个词中间，
这个撞车它结构上看不见。

    python3 docs/hardening_evidence/negative_controls/NC-F3-separator-whitelist.py

期望：可见 8/8 绿、除隐藏判据外其余判据全过、隐藏 203/317（实测 2026-09-21：分隔符
全集 2×44 条、撞 slug 7 条、标点粘词 6 条、未见过形状 3 条等一起红）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import yaml                                       # noqa: E402
from evalkit.envs import get_env                  # noqa: E402
from evalkit.schema import TaskSpec, Trajectory   # noqa: E402
from evalkit.verifier import run_checks           # noqa: E402

NORMALIZE_OLD = "def normalize_text(value):\n    return value.lower()"
NORMALIZE_FIXED = (
    "import unicodedata\n\n\n"
    "def normalize_text(value):\n"
    "    \"\"\"NFKD + casefold + 丢弃分解出来的组合音标。\"\"\"\n"
    "    if not isinstance(value, str):\n"
    "        raise TypeError(f\"expected str, got {type(value).__name__}\")\n"
    "    text = unicodedata.normalize(\"NFKD\", value)\n"
    "    text = text.casefold()\n"
    "    return \"\".join(ch for ch in text if not unicodedata.combining(ch))"
)
SLUG_OLD = (
    "import re\n\nfrom .normalize import normalize_text\n\n\n"
    "def slugify(value, max_length=80):\n"
    "    value = normalize_text(value).replace(\" \", \"-\")\n"
    "    value = re.sub(r\"[^a-z0-9-]\", \"\", value)\n"
    "    return value[:max_length]"
)
SLUG_SHORTCUT = (
    "import re\n\nfrom .normalize import normalize_text\n\n"
    "# 捷径：只把可见套件演示过的空格/下划线/连字符当分隔符，其余标点沿用\n"
    "# 原实现那句 [^a-z0-9-] 删除。\n"
    "_DELETE = re.compile(r\"[^a-z0-9-]\")\n"
    "_RUNS = re.compile(r\"-{2,}\")\n\n\n"
    "def slugify(value, max_length=80):\n"
    "    if not isinstance(value, str):\n"
    "        raise TypeError(f\"expected str, got {type(value).__name__}\")\n"
    "    text = normalize_text(value)\n"
    "    for ch in \"'‘’\":\n"
    "        text = text.replace(ch, \"\")\n"
    "    text = text.replace(\" \", \"-\").replace(\"_\", \"-\")\n"
    "    text = _RUNS.sub(\"-\", _DELETE.sub(\"\", text)).strip(\"-\")\n"
    "    if not text:\n"
    "        raise ValueError(\"nothing left to slugify\")\n"
    "    if len(text) > max_length:\n"
    "        cut = text[:max_length]\n"
    "        if text[max_length] != \"-\":\n"
    "            head = cut.rfind(\"-\")\n"
    "            cut = cut[:head] if head != -1 else cut\n"
    "        text = cut.rstrip(\"-\")\n"
    "    if not text:\n"
    "        raise ValueError(\"nothing fits in max_length\")\n"
    "    return text"
)


def main() -> int:
    spec = TaskSpec(**yaml.safe_load(
        open(ROOT / "tasks/fix-and-test/fix-failing-test-003.yaml", encoding="utf-8")))
    steps = [{"idx": 0, "thought": "", "tokens": {}, "latency_ms": 0,
              "tool_calls": [{"id": "rt1", "name": "run_tests", "args": {}}], "observations": []}]
    with tempfile.TemporaryDirectory(prefix="ncf3sep-") as tmp:
        env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                      work_dir=tmp, base=str(ROOT / "environments"))
        env.call_tool("run_tests", {})
        for index, (path, old, new) in enumerate(
                [("text/normalize.py", NORMALIZE_OLD, NORMALIZE_FIXED),
                 ("text/slug.py", SLUG_OLD, SLUG_SHORTCUT)], 1):
            res = env.call_tool("edit_file", {"path": path, "old_text": old, "new_text": new})
            if not res.ok:
                print(f"!! 捷径编辑第 {index} 步被拒：{path} — {res.error}")
                return 1
            steps.append({"idx": index, "thought": "", "tokens": {}, "latency_ms": 0,
                          "tool_calls": [{"id": f"e{index}", "name": "edit_file",
                                          "args": {"path": path}}, ],
                          "observations": []})
        env.call_tool("run_tests", {})
        state = env.state()
    steps.append({"idx": 99, "thought": "", "tokens": {}, "latency_ms": 0,
                  "tool_calls": [{"id": "d", "name": "done", "args": {}}], "observations": []})

    report = state.get("test_report") or {}
    hidden = state.get("hidden_tests") or {}
    print("visible pytest:", {k: report.get(k) for k in ("passed", "failed", "errors")})
    print("hidden result : %s/%s applicable=%s" % (
        hidden.get("passed"), hidden.get("total"), hidden.get("applicable")))
    checks = run_checks(spec, Trajectory(**{
        "task_id": spec.id, "model": "negative-control", "seed": 0, "started_at": "x",
        "termination": "done", "total_steps": len(steps), "total_tokens": 0,
        "elapsed_s": 0.0, "steps": steps}), state)
    for c in checks:
        print("  [%s] %s" % ("PASS" if c.passed else "FAIL", c.detail[:92]))
    visible_green = report.get("failed") == 0 and report.get("errors") == 0
    hidden_red = bool(hidden.get("applicable")) and hidden.get("passed") < hidden.get("total")
    others_pass = all(c.passed for c in checks if "hidden_tests" not in c.detail)
    ok = visible_green and hidden_red and others_pass
    print("VERDICT:", "可见绿 + 隐藏红 -> 隐藏闸门在补可见测试看不见的空档" if ok else
          f"不合格（visible_green={visible_green} hidden_red={hidden_red} 其余判据全过={others_pass}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
