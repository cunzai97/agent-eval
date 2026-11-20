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
    "INV_X1_D28":   dict(area="0.412", inputs=1, outputs=1, timings=1, rise_blocks=1),
    "NAND2_X1_D28": dict(area="0.588", inputs=2, outputs=1, timings=2, rise_blocks=2),
    "DFFQ_X1_D28":  dict(area="2.436", inputs=2, outputs=1, timings=5, rise_blocks=1),
}

IDX1 = "0.001, 0.012, 0.023, 0.034, 0.045, 0.056, 0.067"
IDX2 = IDX1
TABLE_N = 7


def rnd(*parts: str, lo: float = 0.0001, hi: float = 0.9, digits: int = 6) -> float:
    """确定性伪随机：同样的输入永远得到同样的数。"""
    h = hashlib.sha256(("|".join((SEED, *parts))).encode()).digest()
    n = int.from_bytes(h[:8], "big") / 2**64
    return round(lo + (hi - lo) * n, digits)


def table_block(name: str, cell: str, pin: str, idx: int, indent: str = "        ") -> list[str]:
    """一个 7x7 表：index_1 / index_2 / 多行 values（反斜杠续行）。"""
    out = [f'{indent}index_1 ("{IDX1}");', f'{indent}index_2 ("{IDX2}");', f"{indent}values ( \\"]
    rows = []
    for r in range(TABLE_N):
        vals = ", ".join(f"{rnd(name, cell, pin, str(idx), str(r), str(c)):.6f}" for c in range(TABLE_N))
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


def cell_block(name: str, spec: dict) -> tuple[list[str], int]:
    """写一个 cell：input pins → output pin（挂全部 timing）→ internal_power。"""
    ins = [chr(ord("A") + i) for i in range(spec["inputs"])]
    is_seq = name.startswith(("DFF", "SDF", "LATCH"))
    if is_seq and spec["inputs"] >= 2:
        ins = ["D", "CK"]
    outs = ["Q"] if is_seq else ["Z"]

    lines = [f"  cell ({name}) {{",
             f"    area : {spec['area']};",
             "    dont_touch : true;",
             "    pg_pin (VDD) {", "      pg_type : primary_power;", '      voltage_name : "VDD";', "    }",
             "    pg_pin (VSS) {", "      pg_type : primary_ground;", '      voltage_name : "VSS";', "    }"]
    for p in ins:
        lines += [f"    pin ({p}) {{",
                  "      direction : input;",
                  f"      capacitance : {rnd('cap', name, p):.6f};",
                  f"      rise_capacitance : {rnd('rcap', name, p):.6f};",
                  f"      fall_capacitance : {rnd('fcap', name, p):.6f};",
                  f"      max_transition : {rnd('mt', name, p, lo=0.2, hi=4.0, digits=4):.4f};",
                  "    }"]
    out_pin = outs[0]
    lines += [f"    pin ({out_pin}) {{",
              "      direction : output;",
              f"      max_capacitance : {rnd('mcap', name, out_pin, lo=0.05, hi=2.0, digits=4):.4f};",
              f'      function : "{logic_expr(name, ins)}";']
    total = 0
    for i in range(spec["timings"]):
        has_rise = i < spec["rise_blocks"]
        related = ins[i % len(ins)]
        lines += ["      timing () {", f'        related_pin : "{related}";']
        if is_seq and i % 3 == 0:
            lines += ["        timing_type : setup_rising;"]
        else:
            lines += [f"        timing_sense : {'positive_unate' if i % 2 else 'negative_unate'};"]
        tables = (["cell_rise", "cell_fall"] if has_rise else []) + [
            "rise_transition", "fall_transition", "rise_constraint", "fall_constraint"]
        for t in tables:
            lines += [f"        {t} (delay_template_7x7) {{"] + table_block(t, name, out_pin, i) + ["        }"]
        lines += ["      }"]
        total += 1
    lines += ["      internal_power () {",
              f"        rise_power (power_template_7x7) {{"]
    lines += table_block("rp", name, out_pin, 0, indent="          ")
    lines += ["        }", f"        fall_power (power_template_7x7) {{"]
    lines += table_block("fp", name, out_pin, 0, indent="          ")
    lines += ["        }", "      }", "    }"]
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

    # timing 分配：点名 cell 用固定结构，其余按确定性抖动分，再把差额摊平，保证总数精确
    special_in = {n: s for n, s in SPECIAL.items() if n in names}
    fixed = sum(s["timings"] for s in special_in.values())
    others = [n for n in names if n not in SPECIAL]
    per = {n: 1 + (hashlib.sha256((SEED + n).encode()).digest()[0] % 40) for n in others}
    diff = (args.timings - fixed) - sum(per.values())
    i = 0
    while diff != 0:
        n = others[i % len(others)]
        if diff > 0:
            per[n] += 1
            diff -= 1
        elif per[n] > 1:
            per[n] -= 1
            diff += 1
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
                spec = dict(area=f"{rnd('area', n, lo=0.2, hi=8.0, digits=3):.3f}",
                            inputs=1 + hashlib.sha256((SEED + n).encode()).digest()[0] % 4,
                            outputs=1,
                            timings=tcount,
                            rise_blocks=max(1, tcount // 2))
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
