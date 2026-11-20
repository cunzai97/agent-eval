"""Mutant matrix for fix-failing-test-002 (snapshot fix-002): 每份捷径各被哪条隐藏用例抓住。

先把参考解（`references/fix-failing-test-002.json` 的两次 edit_file）灌进干净快照，
再叠加**一处**变异，然后分别跑可见套件与隐藏模块。跑在 host pytest 上（3.12.3，与
`agent-eval-pytest:3.12` 同 minor、decimal 属标准库），比每份变异起一次 docker 快两个数量级；
参考解在本机上同样是可见 8/8、隐藏 9/9。

    python3 docs/hardening_evidence/negative_controls/NC-fix-failing-test-002-mutant-matrix.py

实测（本机跑通，六份变异**全部**是"可见 8/8 全绿 + 隐藏模块红"）：

| 变异 | 隐藏 | 抓住它的用例 |
| --- | --- | --- |
| rules-comment-only | 7/9 | `rules_module_carries_the_declared_policy` + `convert_uses_the_rules_module_scale` |
| decimal-str-float | 8/9 | `amounts_keep_every_digit_without_transiting_binary_float` |
| plus-half-truncate | 6/9 | `negative_half_way_amounts_round_away_from_zero` + 另两条 |
| one-separator-group | 8/9 | `supported_notation_is_not_narrowed` |
| default-currency-drift | 7/9 | `convert_uses_the_rules_module_scale` + `public_signature_is_unchanged` |
| strip-parens-either-end | 8/9 | `malformed_amounts_are_rejected_with_value_error` |

`rules-comment-only` 是这道题最要紧的一份：判据只要求 `money/rules.py` 出现在
files_changed 里，所以"rules 里只加一行注释、策略整套搬进 convert.py"能同时满足
四条原有 state_assert——可见与文件级通道都看不见它。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SNAP = ROOT / "environments/code_repo/snapshots/fix-002"
HIDDEN = ROOT / "evalkit/envs/hidden_tests/fix-002.py"
REF = json.load(open(ROOT / "docs/hardening_evidence/references/fix-failing-test-002.json",

                     encoding="utf-8"))

RULES_BUGGY = """from decimal import ROUND_DOWN


# NOTE: the real policy now lives in convert.py; this module is kept for its importers.
MINOR_UNITS = {"USD": 2, "JPY": 2, "KWD": 2}
ROUNDING = ROUND_DOWN


def scale(currency):
    return 10 ** MINOR_UNITS[currency]
"""
CONVERT_FACADE = """from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_MIN = {"USD": 2, "JPY": 0, "KWD": 3}


def to_minor_units(value, currency="USD"):
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = text.replace("$", "").replace(",", "").strip()
    try:
        amount = Decimal(text)
    except (ArithmeticError, InvalidOperation, TypeError, ValueError):
        raise ValueError(f"invalid amount: {value!r}") from None
    if not amount.is_finite():
        raise ValueError(f"invalid amount: {value!r}")
    if currency not in _MIN:
        raise ValueError(f"unknown currency: {currency!r}")
    units = (amount * (10 ** _MIN[currency])).to_integral_value(rounding=ROUND_HALF_UP)
    return -int(units) if negative else int(units)
"""

MUTANTS = {
    # M1: rules.py satisfied by a comment, policy duplicated in convert.py (facade bypass)
    "rules-comment-only": lambda w: (
        (w / "money/rules.py").write_text(RULES_BUGGY, encoding="utf-8"),
        (w / "money/convert.py").write_text(CONVERT_FACADE, encoding="utf-8")),
    # M2: parse through binary float
    "decimal-str-float": lambda w: _sub(
        w / "money/convert.py", "        amount = Decimal(text)",
        "        amount = Decimal(str(float(text)))"),
    # M3: fake ROUND_HALF_UP via +0.5 truncation  (covered by the on-disk NC)
    "plus-half-truncate": lambda w: _sub(
        w / "money/convert.py",
        "    units = (amount * scale(currency)).to_integral_value(rounding=ROUNDING)",
        "    units = int(amount * scale(currency) + Decimal('0.5'))"),
    # M4: strip only the first thousands separator group
    "one-separator-group": lambda w: (
        _sub(w / "money/convert.py", "from decimal import Decimal, InvalidOperation",
             "import re  # noqa\nfrom decimal import Decimal, InvalidOperation"),
        _sub(w / "money/convert.py", '    text = text.replace("$", "").replace(",", "").strip()',
             '    text = text.replace("$", "").strip()\n'
             '    text = re.sub(r"(?<=\\d),(?=\\d{3}\\b)", "", text, count=1)\n'
             '    text = text.strip()')),
    # M5: default currency drifts to another scale
    "default-currency-drift": lambda w: _sub(
        w / "money/convert.py", 'def to_minor_units(value, currency="USD"):',
        'def to_minor_units(value, currency="usd"):'),
    # M6: peel parentheses from either end independently (unbalanced stays legal)
    "strip-parens-either-end": lambda w: _sub(
        w / "money/convert.py",
        '    negative = text.startswith("(") and text.endswith(")")\n'
        '    if negative:\n'
        '        text = text[1:-1].strip()',
        '    negative = text.startswith("(") or text.endswith(")")\n'
        '    text = text.strip("()").strip()'),
}


def _sub(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, f"anchor not found in {path.name}: {old[:50]}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _run(ws, *targets):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "-q", "-o", "addopts=",
         "-p", "no:cacheprovider", "--no-header", "-rf", "--tb=no"],
        cwd=ws, capture_output=True, text=True, timeout=120)
    out = proc.stdout + proc.stderr
    tally = {}
    for key in ("passed", "failed", "error"):
        m = re.search(r"(\d+) %s" % key, out)
        tally[key] = int(m.group(1)) if m else 0
    names = re.findall(r"FAILED \S+::(\w+)", out)
    return tally, sorted(set(names)), out


def build():
    tmp = Path(tempfile.mkdtemp(prefix="mut-f002-"))
    ws = tmp / "workspace"
    shutil.copytree(SNAP, ws)
    for call in REF["calls"]:
        if call[0] != "edit_file":
            continue
        args = call[1]
        p = ws / args["path"]
        text = p.read_text(encoding="utf-8")
        assert args["old_text"] in text, "reference anchor missing: " + args["path"]
        p.write_text(text.replace(args["old_text"], args["new_text"], 1), encoding="utf-8")
    shutil.copy2(HIDDEN, ws / "test_agent_eval_hidden.py")
    return tmp, ws


def main() -> int:
    only = sys.argv[1:] or list(MUTANTS)
    base_tmp, base = build()
    tally, names, _ = _run(base, "tests/test_convert.py")
    print("reference solution : visible %s  %s" % (tally, names))
    tally, names, _ = _run(base, "test_agent_eval_hidden.py")
    print("reference solution : hidden   %s  %s" % (tally, names))
    shutil.rmtree(base_tmp, ignore_errors=True)
    for label in only:
        mutate = MUTANTS[label]
        tmp, ws = build()
        try:
            mutate(ws)
        except AssertionError as exc:
            print("%-24s MUTATION ANCHOR FAILED: %s" % (label, exc))
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        v_t, v_n, _ = _run(ws, "tests/test_convert.py")
        h_t, h_n, h_out = _run(ws, "test_agent_eval_hidden.py")
        print("%-24s visible %s | hidden %d passed/%d failed | %s"
              % (label, v_t, h_t["passed"], h_t["failed"],
                 ",".join(n.replace("test_", "") for n in h_n) or "-"))
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
