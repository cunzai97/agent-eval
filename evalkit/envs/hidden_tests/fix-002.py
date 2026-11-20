"""Hidden checks for snapshot fix-002（多币种最小单位 + 十进制 ROUND_HALF_UP）。

可见套件（tests/test_convert.py，8 条）已经钉住：三张表值本身（JPY=0、KWD=3）、
正数一侧的 ROUND_HALF_UP、会计负数、一个非法值、一个未知币种、以及一个
"不得经过 binary float" 的样例。它钉不住的是下面这些，而它们恰恰是捷径的落点：

1. **规则模块被"改一行注释"糊过去**。判据只要求 `money/rules.py` 出现在
   files_changed 里，所以把整套逻辑写进 convert.py、rules 里的
   `MINOR_UNITS = {"JPY": 2}` 与 `ROUNDING = ROUND_DOWN` 原封不动，可见测试照样全绿。
2. **负数的半分**。可见套件里没有任何"负数 + 恰好半分"的输入，而
   `int(x + 0.5)`、Python 的 `round()`（HALF_EVEN）、HALF_DOWN、HALF_CEILING 这些写法
   在正数上与 ROUND_HALF_UP 完全一致，只在负数上分道——ROUND_HALF_UP 是
   **远离零**：-12.5 分 → -13。这条缺陷在可见通道上没有任何影子。
3. **经过 binary float**。可见那条 16 位有效数字的用例恰好落在 float64 能表达的
   边界内（`1000000000000.01` 的最短往返仍然等于原串），所以
   `Decimal(str(float(text)))` 这类"绕一圈二进制浮点再转回来"的写法能骗过它；
   超过 17 位有效数字的金额立刻被 float 精度截断（实测
   `1234567890123456789012.34` → `…669025792`）。
4. **记号支持被改窄**：只剥一组千分位、只认前置 `$`（`-$12.34` / `$-12.34` 在修复前的
   实现里是能跑的，属于回归）、括号与 `$` 组合、首尾空白。
5. **拒绝口径漏气**：非法值必须抛 ValueError（快照里未修的表会把未知币种抛成
   KeyError），括号不配对、`12.34.56`、`nan`、空串都得拒。

定标口径（实测，不是手推）：把 35 份历史尝试的 edit_file 调用重放到干净快照，得到
31 份"可见 8 条全绿"的实现（其中 24 份历史判对、7 份是流程闸门放宽后新准入的），
逐条灌进本模块的每一个期望值——31/31 全部给出同一个数，零误杀（`tools/replay_gate.py
fix-failing-test-002 --only-passed` 复核：24/24 存活，全为 9/9）；未修快照 2/9，7 条红。
另构造六份单点变异，逐份复验为"可见 8/8 全绿 + 本模块红"，且被不同用例抓住：
rules.py 只加注释而逻辑搬进 convert（→ `rules_module_carries_the_declared_policy` +
`convert_uses_the_rules_module_scale`）、`Decimal(str(float(x)))` 绕一圈二进制浮点
（→ `amounts_keep_every_digit_without_transiting_binary_float`）、`x + 0.5` 取整冒充
HALF_UP（→ `negative_half_way_amounts_round_away_from_zero` + 另两条，另有一份独立 NC
`NC-fix-failing-test-002-fake-half-up.py`）、只剥一组千分位
（→ `supported_notation_is_not_narrowed`）、默认币种漂移
（→ `public_signature_is_unchanged` + `convert_uses_the_rules_module_scale`）、括号两端
各自剥导致 `(12.34` 被当成合法输入（→ `malformed_amounts_are_rejected_with_value_error`）。
这六份变异是**可重跑的**：`negative_controls/NC-fix-failing-test-002-mutant-matrix.py`。
期望值的分位行为矩阵在 host CPython 3.12.3（与
`agent-eval-pytest:3.12` 同 minor、decimal 为标准库）上跑通后，本模块与两份 NC 再在
该镜像内整体复跑确认。
"""
import inspect
from decimal import ROUND_HALF_UP, Decimal

import pytest

from money import rules
from money.convert import to_minor_units

MINOR = {"USD": 2, "JPY": 0, "KWD": 3}


# ---------------------------------------------------------------------------
# 1. 规则模块本身必须被修，而不是"改过就算"
# ---------------------------------------------------------------------------
def test_rules_module_carries_the_declared_policy():
    assert getattr(rules, "MINOR_UNITS", None) == MINOR, "money/rules.py 的最小单位表仍是错的（JPY=2/KWD=2）"
    for code, exponent in MINOR.items():
        assert rules.scale(code) == 10 ** exponent
    assert rules.ROUNDING is ROUND_HALF_UP, "规则模块里写明的舍入口径还是 ROUND_DOWN"


def test_convert_uses_the_rules_module_scale():
    for code in MINOR:
        assert to_minor_units("1", code) == rules.scale(code)
        assert to_minor_units("0", code) == 0
    assert to_minor_units("1") == rules.scale("USD"), "默认币种仍是 USD"


# ---------------------------------------------------------------------------
# 2. 半分一律远离零：正数用例可见套件已有，负数一条都没有
# ---------------------------------------------------------------------------
def test_positive_half_way_amounts_round_up():
    assert to_minor_units("1.005", "USD") == 101
    assert to_minor_units("2.675", "USD") == 268
    assert to_minor_units("0.125", "USD") == 13
    assert to_minor_units("0.5", "JPY") == 1
    assert to_minor_units("1.5", "JPY") == 2
    assert to_minor_units("0.0005", "KWD") == 1
    assert to_minor_units("0.5005", "KWD") == 501


def test_negative_half_way_amounts_round_away_from_zero():
    assert to_minor_units("(2.5)", "JPY") == -3, "ROUND_HALF_UP 是远离零，不是向正无穷"
    assert to_minor_units("-2.5", "JPY") == -3
    assert to_minor_units("-0.5", "JPY") == -1
    assert to_minor_units("-1.005", "USD") == -101
    assert to_minor_units("-$0.125", "USD") == -13
    assert to_minor_units("(0.0005)", "KWD") == -1
    assert to_minor_units("-0.0005", "KWD") == -1
    # 非半分处不得被"顺手多进一位"
    assert to_minor_units("-1.004", "USD") == -100
    assert to_minor_units("-2.51", "JPY") == -3
    assert to_minor_units("-2.49", "JPY") == -2


# ---------------------------------------------------------------------------
# 3. 金额不得经过 binary float
# ---------------------------------------------------------------------------
def test_amounts_keep_every_digit_without_transiting_binary_float():
    assert to_minor_units("1234567890123456789012.34", "USD") == 123456789012345678901234
    assert to_minor_units("1234567890123456789012.345", "USD") == 123456789012345678901235
    assert to_minor_units("(1234567890123456789012.345)", "USD") == -123456789012345678901235
    assert to_minor_units("0.0000000000000000000001", "USD") == 0
    assert to_minor_units("123456789.123456789", "KWD") == 123456789123
    assert to_minor_units("9007199254740993.005", "USD") == 900719925474099301


# ---------------------------------------------------------------------------
# 4. 支持记号：千分位多组、$ 与负号的先后顺序、空白、会计负数组合
# ---------------------------------------------------------------------------
def test_supported_notation_is_not_narrowed():
    assert to_minor_units("1,234,567.89", "USD") == 123456789
    assert to_minor_units("100,000", "JPY") == 100000
    assert to_minor_units("($1,234,567.891)", "USD") == -123456789
    assert to_minor_units("(1,000)", "JPY") == -1000
    assert to_minor_units("-$12.34", "USD") == -1234      # 修复前就能解析，不得改窄
    assert to_minor_units("$-12.34", "USD") == -1234
    assert to_minor_units("  19.99  ", "USD") == 1999
    assert to_minor_units("$1.005", "USD") == 101


# ---------------------------------------------------------------------------
# 5. 拒绝口径
# ---------------------------------------------------------------------------
def test_malformed_amounts_are_rejected_with_value_error():
    # 千分位是否要求三位一组不作要求（实测 31 份可见全绿的实现一律宽松剥逗号），
    # 这里只钉"结构上根本不是数字"的输入必须抛 ValueError。
    for bad in ("", "   ", "not-money", "$", "12.34.56", "(12.34", "1.2.3.4", "nan", "1..2", "1 2"):
        with pytest.raises(ValueError):
            to_minor_units(bad, "USD")


def test_unknown_currency_is_rejected_with_value_error():
    # 只钉"这张表里没有的代码"，不钉大小写归一化——把 "usd" 也接受掉是一种合理扩展，
    # 而 if/elif 硬编码币种（对任何未知输入默认按 USD 处理）已经被 XDR/AAA/空串挡住。
    for code in ("ABC", "XDR", "AAA", ""):
        with pytest.raises(ValueError):
            to_minor_units("1", code)


# ---------------------------------------------------------------------------
# 6. 公开签名没动
# ---------------------------------------------------------------------------
def test_public_signature_is_unchanged():
    params = list(inspect.signature(to_minor_units).parameters)
    assert params == ["value", "currency"], params
    assert inspect.signature(to_minor_units).parameters["currency"].default == "USD"
    assert to_minor_units(value="19.99", currency="USD") == 1999
    assert to_minor_units("19.99", currency="USD") == 1999
    assert list(inspect.signature(rules.scale).parameters) == ["currency"]
    assert isinstance(to_minor_units(7, "USD"), int)
    assert to_minor_units(7, "USD") == 700
    assert to_minor_units(Decimal("1.005"), "USD") == 101
    assert to_minor_units(Decimal("-0.0005"), "KWD") == -1
