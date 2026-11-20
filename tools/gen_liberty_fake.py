#!/usr/bin/env python3
"""生成一个与真实 PDK 库同体量、同结构的**合成** Liberty 文件。

用途：liberty-extract-001 需要一个 60MB 级、结构真实（嵌套 pin/timing、多行 values、
cell_rise 分散在多个 timing block）的输入，但不能把任何厂商库放进仓库。
这里用固定种子生成一份纯虚构的库：库名、cell 名、数值全是造的，语法完全合法。

体量与真实库对齐（1039 个 cell、13760 个 timing group、60MB 级），
所以考的还是"能不能从大文件里流式提取"，不因为数据变小而变简单。

用法：
    python3 tools/gen_liberty_fake.py [--out PATH] [--cells N] [--timings N]
默认输出 assets/liberty/demo28lp_base_hvt_tt_nominal_max_1p00v_25c.lib。
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

SEED = "demo28-20261120"
LIB_NAME = "demo28lp_base_hvt_tt_nominal_max_1p00v_25c"

BASE_CELLS = [
    "INV", "INVD", "BUF", "BUFD", "CLKINV", "CLKBUF", "CLKAND2", "CLKOR2",
    "NAND2", "NAND3", "NAND4", "NOR2", "NOR3", "NOR4", "AND2", "AND3",
    "OR2", "OR3", "AOI21", "AOI22", "AOI211", "AOI31", "OAI21", "OAI22",
    "OAI211", "OAI31", "MUX2", "MUX4", "XOR2", "XNOR2", "XNOR3", "HA",
    "FA", "ADDF", "ADDH", "DFFQ", "DFFR", "DFFS", "DFFRS", "SDFQ",
    "SDFR", "LATCH", "LATCHR", "ICG", "ICGDS", "TIEHI", "TIELO", "DEL",
    "DELN", "BHD", "SL", "IMUX", "IMUX4", "OA21", "OA22", "AO21", "AO22",
    "NBUF", "PINV", "TBUF", "MUXI2", "MUXI4", "CMP2", "ADD2", "ADD4",
    "SUB2", "SHF2", "SHF4", "DEC2", "DEC3", "ENC2", "PAR2", "PAR4",
    "MAJ3", "MIN3", "ISO", "LEVL", "RETN", "EDGE", "PULSE", "SYNC",
]  # 80 个
DRIVES = ["X0P5", "X0P7", "X1", "X1P4", "X2", "X3", "X4", "X6",
          "X8", "X12", "X16", "X20", "X24"]  # 13 档 -> 1040 个候选名

# 题目点名要解析的三个 cell：结构固定，断言好写
SPECIAL = {
    "INV_X1_D28":   dict(area="0.412", inputs=1, outputs=1, timings=1,
                         kinds=["delay"]),
    "NAND2_X1_D28": dict(area="0.588", inputs=2, outputs=1, timings=2,
                         kinds=["delay", "delay"]),
    "DFFQ_X1_D28":  dict(area="2.436", inputs=2, outputs=1, timings=5,
                         kinds=["delay", "constraint", "constraint", "single", "constraint"]),
}

IDX1 = "0.001, 0.012, 0.023, 0.034, 0.045, 0.056, 0.067"
IDX2 = IDX1
TABLE_N = 7


def rnd(*parts: str, lo: float = 0.0001, hi: float = 0.9, digits: int = 6) -> float:
    """确定性伪随机：同样的输入永远得到同样的数。"""
    h = hashlib.sha256(("|".join((SEED, *parts))).encode()).digest()
    n = int.from_bytes(h[:8], "big") / 2**64
    return round(lo + (hi - lo) * n, digits)


def fmt_num(v: float) -> str:
    """像真实库那样混用 3~7 位有效数字。"""
    dec = 5 + int(v * 1e6) % 3          # 5~7 位小数
    return f"{v:.{dec}f}"


def table_block(name: str, cell: str, pin: str, idx: int, indent: str = "        ") -> list[str]:
    """一个 7x7 表：index_1 / index_2 / 多行 values（反斜杠续行）。"""
    out = [f'{indent}index_1 ("{IDX1}");', f'{indent}index_2 ("{IDX2}");', f"{indent}values ( \\"]
    rows = []
    for r in range(TABLE_N):
        vals = ", ".join(fmt_num(rnd(name, cell, pin, str(idx), str(r), str(c)))
                         for c in range(TABLE_N))
        rows.append(f'{indent}  "{vals}"')
    out.append(", \\\n".join(rows))
    out.append(f"{indent});")
    return out


def logic_expr(name: str, ins: list[str]) -> str:
    a = ins[0]
    b = ins[1] if len(ins) > 1 else ins[0]
    if name.startswith(("INV", "CLKINV", "NBUF", "PINV")):
        return f"!{a}"
    if name.startswith(("NAND", "AOI", "OAI")):
        return f"!({a} {b})" if not name.startswith("NAND") else f"!({a} {b})"
    if name.startswith("NOR"):
        return f"!({a} | {b})"
    if name.startswith(("AND", "CLKAND")):
        return f"({a} {b})"
    if name.startswith(("OR", "CLKOR")):
        return f"({a} | {b})"
    if name.startswith("XOR"):
        return f"({a} ^ {b})"
    if name.startswith("XNOR"):
        return f"!({a} ^ {b})"
    if name.startswith("MUX"):
        return f"({b} ? {a} : 0)"
    return f"({a} {b})"


IN_HIST = [0]*9 + [1]*127 + [2]*186 + [3]*387 + [4]*184 + [5]*36 + [6]*37 + [7]*26 + [8]*16
OUT_HIST = [0]*8 + [1]*930 + [2]*91 + [3]*10


def pin_count(cell: str, which: str) -> int:
    """input/output pin 数按真实库的直方图采样，且确定性。"""
    hist = IN_HIST if which == "in" else OUT_HIST
    h = hashlib.sha256(f"{SEED}|{which}|{cell}".encode()).digest()
    n = hist[int.from_bytes(h[:4], "big") % len(hist)]
    # 0 个 pin 的 cell 没法挂 timing，按 1 处理（真实库里这种不到 1%）
    return n if n > 0 else 1


def timing_kind(cell: str, idx: int) -> str:
    """真实库里 7640 个 timing 是 4 表（delay）、4000 个 2 表（constraint）、2120 个 1 表。"""
    h = hashlib.sha256(f"{SEED}|kind|{cell}|{idx}".encode()).digest()[0] % 100
    if h < 56:
        return "delay"
    if h < 85:
        return "constraint"
    return "single"


def cell_block(name: str, spec: dict) -> tuple[list[str], int]:
    """写一个 cell：input pins（各带 power）→ 输出 pin（挂全部 timing）→ leakage。"""
    ins = [chr(ord("A") + i) for i in range(spec["inputs"])]
    is_seq = name.startswith(("DFF", "SDF", "LATCH"))
    if is_seq and spec["inputs"] >= 2:
        ins = ["D", "CK"]
    outs = ["Q"] if is_seq else (["Z"] if spec["outputs"] <= 1
                                 else [f"Z{i}" for i in range(spec["outputs"])])

    lines = [f"  cell ({name}) {{",
             f"    area : {spec['area']};",
             "    dont_touch : true;",
             "    pg_pin (VDD) {", "      pg_type : primary_power;", '      voltage_name : "VDD";', "    }",
             "    pg_pin (VSS) {", "      pg_type : primary_ground;", '      voltage_name : "VSS";', "    }"]
    for pin in ins:
        lines += [f"    pin ({pin}) {{",
                  "      direction : input;",
                  f"      capacitance : {rnd('cap', name, pin):.6f};",
                  f"      rise_capacitance : {rnd('rcap', name, pin):.6f};",
                  f"      fall_capacitance : {rnd('fcap', name, pin):.6f};",
                  f"      max_transition : {rnd('mt', name, pin, lo=0.2, hi=4.0, digits=4):.4f};",
                  "      internal_power () {",
                  "        rise_power (power_template_7x7) {"]
        lines += table_block("ipr", name, pin, 0, indent="          ")
        lines += ["        }", "        fall_power (power_template_7x7) {"]
        lines += table_block("ipf", name, pin, 0, indent="          ")
        lines += ["        }", "      }", "    }"]

    out_pin = outs[0]
    lines += [f"    pin ({out_pin}) {{",
              "      direction : output;",
              f"      max_capacitance : {rnd('mcap', name, out_pin, lo=0.05, hi=2.0, digits=4):.4f};",
              f'      function : "{logic_expr(name, ins)}";']
    total = 0
    for i in range(spec["timings"]):
        kind = spec["kinds"][i] if spec.get("kinds") else timing_kind(name, i)
        related = ins[i % len(ins)]
        lines += ["      timing () {", f'        related_pin : "{related}";']
        if is_seq and i % 3 == 0:
            lines += ["        timing_type : setup_rising;"]
        else:
            lines += [f"        timing_sense : {'positive_unate' if i % 2 else 'negative_unate'};"]
        if kind == "delay":
            tables = ["cell_rise", "cell_fall", "rise_transition", "fall_transition"]
        elif kind == "constraint":
            tables = ["rise_constraint", "fall_constraint"]
        else:
            tables = ["rise_transition"]
        for tbl in tables:
            lines += [f"        {tbl} (delay_template_7x7) {{"] + table_block(tbl, name, out_pin, i) + ["        }"]
        lines += ["      }"]
        total += 1
    lines += ["      internal_power () {", "        rise_power (power_template_7x7) {"]
    lines += table_block("rp", name, out_pin, 0, indent="          ")
    lines += ["        }", "        fall_power (power_template_7x7) {"]
    lines += table_block("fp", name, out_pin, 0, indent="          ")
    lines += ["        }", "      }", "    }"]
    for extra in outs[1:]:
        lines += [f"    pin ({extra}) {{", "      direction : output;",
                  f"      max_capacitance : {rnd('mcap', name, extra, lo=0.05, hi=2.0, digits=4):.4f};",
                  f'      function : "{logic_expr(name, ins)}";', "    }"]
    lines += ["    leakage_power () {",
              f"      value : {rnd('lk', name, lo=0.0001, hi=9.9, digits=4):.4f};", "    }", "  }"]
    return lines, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--cells", type=int, default=1039)
    ap.add_argument("--timings", type=int, default=13760)
    args = ap.parse_args()
    out = pathlib.Path(args.out) if args.out else (
        pathlib.Path(__file__).resolve().parents[1] / "assets/liberty" / f"{LIB_NAME}.lib")

    names = [f"{b}_{d}_D28" for b in BASE_CELLS for d in DRIVES][: args.cells]
    if len(names) < args.cells:
        print(f"cell 名不够：需要 {args.cells}，只有 {len(names)}", file=sys.stderr)
        return 2

    # timing 数按真实库（1039 cell / 13760 timing）的直方图采样：
    # 0:10 1:126 2:130 3:92 4:75 5:117 6:81 7:54 8:200 9:59 10:73 其余 11..156
    HIST = ([0]*10 + [1]*126 + [2]*130 + [3]*92 + [4]*75 + [5]*117 + [6]*81
            + [7, 8, 9, 10]*13 + [8, 9]                       # 7-10：54
            + [12, 14, 16, 18, 20]*40                          # 11-20：200
            + [22, 26, 30, 34, 38, 40]*10 + [24, 28]           # 21-40：59+
            + [42, 50, 58, 66, 74, 80]*12 + [45]               # 41-80：73
            + [82, 96, 110, 124, 138]*4 + [148, 156])          # >80：22

    special_in = {n: s for n, s in SPECIAL.items() if n in names}
    fixed = sum(s["timings"] for s in special_in.values())
    others = [n for n in names if n not in special_in]
    pool = (HIST + [None] * max(0, len(others) - len(HIST)))[: len(others)]
    import random as _random
    _random.Random(SEED).shuffle(pool)
    per = {}
    for idx, n in enumerate(others):
        v = pool[idx]
        per[n] = v if v is not None else 11 + hashlib.sha256((SEED + n).encode()).digest()[0] % 146
    # 把总数摊到总数可调的那部分，保证 timing_group_count 精确
    diff = (args.timings - fixed) - sum(per.values())
    i = 0
    while diff != 0:
        n = others[i % len(others)]
        if diff > 0:
            per[n] += 1; diff -= 1
        elif per[n] > 0:
            per[n] -= 1; diff += 1
        i += 1
    per.update({n: s["timings"] for n, s in special_in.items()})

    head = [
        "/* ----------------------------------------------------------------------------",
        "/* 合成 Liberty 库（demo28lp）。",
        "/* 全部 cell 定义、面积与时序表均由程序生成，是不对应任何真实工艺或厂商的虚构数据，",
        "/* 仅用于评测工具链的解析与流式提取测试。无第三方版权内容。",
        "/* ----------------------------------------------------------------------------",
        f"library ({LIB_NAME}) {{",
        "  technology (cmos);",
        "  delay_model : table_lookup;",
        '  time_unit : "1ns";',
        '  voltage_unit : "1V";',
        '  current_unit : "1mA";',
        "  capacitive_load_unit (1, pf);",
        '  pulling_resistance_unit : "1kohm";',
        '  leakage_power_unit : "1uW";',
        "  nom_process : 1.0;",
        "  nom_temperature : 25.0;",
        "  nom_voltage : 1.0;",
        '  operating_conditions ("tt_nominal_max_1p00v_25c") {',
        "    process : 1.0;",
        "    temperature : 25.0;",
        "    voltage : 1.0;",
        "    tree_type : balanced_tree;",
        "  }",
        "  default_operating_conditions : tt_nominal_max_1p00v_25c;",
        "  lu_table_template (delay_template_7x7) {",
        "    variable_1 : input_net_transition;",
        "    variable_2 : total_output_net_capacitance;",
        f'    index_1 ("{IDX1}");',
        f'    index_2 ("{IDX2}");',
        "  }",
        "  lu_table_template (power_template_7x7) {",
        "    variable_1 : input_net_transition;",
        "    variable_2 : total_output_net_capacitance;",
        "  }",
    ]

    total_timing = 0
    with out.open("w", encoding="utf-8") as f:
        f.write("\n".join(head) + "\n")
        for n in names:
            tcount = per[n]
            spec = SPECIAL.get(n)
            if spec is None:
                spec = dict(area=f"{0.351 + 14.625 * (rnd('area', n) / 0.9) ** 3.6:.3f}",
                            inputs=pin_count(n, "in"), outputs=pin_count(n, "out"),
                            timings=tcount)
            lines, written = cell_block(n, spec)
            total_timing += written
            f.write("\n".join(lines) + "\n")
        f.write("}\n")

    size = out.stat().st_size
    print(f"生成 {out}")
    print(f"  cell={len(names)}  timing_group={total_timing}  大小={size/1e6:.1f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
