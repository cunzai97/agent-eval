"""Negative control for fix-failing-test-002 (snapshot fix-002): the shortcut the
visible pytest suite cannot see.

假半进位：表与口径都按题面改对了（USD 2 / JPY 0 / KWD 3，`rules.ROUNDING = ROUND_HALF_UP`），
但 convert.py 不真的用这个口径，而是手写 `int(amount * scale + Decimal("0.5"))`。
Decimal 全程十进制，所以"不得经过 binary float"那条也满足；而且可见套件里**没有任何
"负数 + 恰好半分"的输入**，于是 8 条用例全绿。

十进制 ROUND_HALF_UP 的定义是**远离零**：`-12.5` 分 -> `-13`。`+0.5` 再向零截断等价于
HALF_CEILING，负数半分全部差 1：`-$0.125` -> -12（应 -13）、`-2.5` JPY -> -2（应 -3）、
`(0.0005)` KWD -> 0（应 -1）。对一个多币种最小单位库来说，这正是退款/冲正记账的错。

Edits are applied to a throwaway copy of the snapshot in a temp dir; tests/ is never touched.

    python3 docs/hardening_evidence/negative_controls/NC-fix-failing-test-002-fake-half-up.py

Expected: visible 8/8 green, both named files changed, hidden 6/9
(caught by the negative-tie case, plus the notation and high-precision ones).
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

RULES_OLD = """from decimal import ROUND_DOWN


MINOR_UNITS = {"USD": 2, "JPY": 2, "KWD": 2}
ROUNDING = ROUND_DOWN


def scale(currency):
    return 10 ** MINOR_UNITS[currency]"""
RULES_NEW = """from decimal import ROUND_HALF_UP


MINOR_UNITS = {"USD": 2, "JPY": 0, "KWD": 3}
ROUNDING = ROUND_HALF_UP


def scale(currency):
    try:
        exponent = MINOR_UNITS[currency]
    except KeyError:
        raise ValueError(f"unknown currency: {currency!r}") from None
    return 10 ** exponent"""

CONV_OLD = """from .rules import scale


def to_minor_units(value, currency="USD"):
    text = str(value).strip().replace("$", "").replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    result = int(float(text) * scale(currency))
    return -result if negative else result"""
CONV_NEW = """from decimal import Decimal

from .rules import scale


def to_minor_units(value, currency="USD"):
    text = str(value).strip().replace("$", "").replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        amount = Decimal(text)
    except ArithmeticError:
        raise ValueError(f"invalid amount: {value!r}") from None
    if not amount.is_finite():
        raise ValueError(f"invalid amount: {value!r}")
    # half-up by shifting a half quantum before truncating (all decimal)
    result = int(amount * scale(currency) + Decimal("0.5"))
    return -result if negative else result"""

EDITS = [
    ("money/rules.py", RULES_OLD, RULES_NEW),
    ("money/convert.py", CONV_OLD, CONV_NEW),
]


def main() -> int:
    spec = TaskSpec(**yaml.safe_load(
        open(ROOT / "tasks/fix-and-test/fix-failing-test-002.yaml", encoding="utf-8")))
    steps = [{"idx": -1, "thought": "", "tokens": {}, "latency_ms": 0,
              "tool_calls": [{"id": "rt1", "name": "run_tests", "args": {}}],
              "observations": []}]
    with tempfile.TemporaryDirectory(prefix="nc-fix002-") as tmp:
        env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                      work_dir=tmp, base=str(ROOT / "environments"))
        env.call_tool("run_tests", {})
        for index, (path, old, new) in enumerate(EDITS, 1):
            res = env.call_tool("edit_file", {"path": path, "old_text": old, "new_text": new})
            if not res.ok:
                print(f"!! shortcut edit #{index} on {path} refused: {res.error}")
                return 1
            steps.append({"idx": index, "thought": "", "tokens": {}, "latency_ms": 0,
                          "tool_calls": [{"id": "e%d" % index, "name": "edit_file",
                                          "args": {"path": path, "old_text": old, "new_text": new}}],
                          "observations": []})
        env.call_tool("run_tests", {})
        state = env.state()
    steps.append({"idx": 98, "thought": "", "tokens": {}, "latency_ms": 0,
                  "tool_calls": [{"id": "rt2", "name": "run_tests", "args": {}}], "observations": []})
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
