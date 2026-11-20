#!/usr/bin/env python3
"""Independently recompute liberty-extract-001's expected values from the asset.

Brace/paren/string aware, per the task's own requirement (no naive
"cut at next cell" assumption).
"""
import re
import sys
from pathlib import Path

LIB = (Path(__file__).resolve().parents[1] / "assets/liberty"
       / "demo28lp_base_hvt_tt_nominal_max_1p00v_25c.lib")
CELLS = ("DFFQ_X1_D28", "INV_X1_D28", "NAND2_X1_D28")

TOKEN = re.compile(r"""
      /\*.*?\*/          # block comment
    | "(?:[^"\\]|\\.)*"  # quoted string
    | [A-Za-z_][A-Za-z0-9_]*   # identifier
    | \{|\}|\(|\)|;|,          # punctuation
    | -?\d+\.\d+(?:[eE][-+]?\d+)? |-?\d+(?:[eE][-+]?\d+)?  # number
    | \S
""", re.VERBOSE | re.DOTALL)


def tokens(text):
    """产出 (token 文本, 起始偏移)。偏移用于按原样取回标量值。"""
    for m in TOKEN.finditer(text):
        t = m.group()
        if t.startswith("/*"):
            continue
        yield t, m.start()


def num_list(raw):
    return [float(x) for x in re.findall(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?", raw)]


def main():
    text = LIB.read_text(encoding="utf-8", errors="replace")
    pairs = list(tokens(text))
    toks = [a for a, _ in pairs]
    SPAN = [b for _, b in pairs]

    library = None
    meta = {}
    cells = {}            # name -> dict
    stack = []            # list of (kind, name)
    cur_cell = None
    cell_path = []

    i, n = 0, len(toks)

    def raw_between(a, b):
        """按源文件原样取回 [a, b) 区间内的文本，不经 token 重组。

        "1ns" 会被切成 1 与 ns 两个 token，用空格 join 就得到 "1 ns"，
        而题面断言钉的是源码里的 1ns。标量值必须以原文为准。
        """
        return text[SPAN[a]:SPAN[b]].strip() if a < b else ""

    while i < n:
        t = toks[i]
        # 每轮都取当前栈的实时作用域；旧写法在 key:value 分支里才赋值 kinds，
        # 于是块分支读到的是上一个标量属性的残留，且文件开头若先出现 name(){ 会 NameError。
        kinds = [k for k, _ in stack]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t or "") and i + 1 < n and toks[i + 1] == "(":
            name = t
            # collect args until matching ')'
            j = i + 2
            args = []
            while j < n and toks[j] != ")":
                if toks[j] not in (",",):
                    args.append(toks[j].strip('"'))
                j += 1
            arg = " ".join(args)
            after = toks[j + 1] if j + 1 < n else ""
            if after == "{":
                # a block
                if name == "library" and not stack:
                    library = arg
                elif name == "cell" and [k for k, _ in stack] == ["library"]:
                    cells[arg] = {"area": None, "in": 0, "out": 0, "timing": 0,
                                  "rise_tables": 0, "max_rise": None, "pins": []}
                    cur_cell = arg
                elif cur_cell is not None and kinds[:2] == ["library", "cell"]:
                    if name == "pin" and len(kinds) == 2:
                        cells[cur_cell]["pins"].append(arg)
                    elif name == "timing":
                        cells[cur_cell]["timing"] += 1
                    elif name == "cell_rise":
                        cells[cur_cell]["rise_tables"] += 1
                stack.append((name, arg))
                i = j + 2
                continue
            if after == ";":
                if (name == "values" and cur_cell is not None
                        and "cell_rise" in kinds):
                    nums = num_list(arg)
                    if nums:
                        c = cells[cur_cell]
                        c["max_rise"] = max(c["max_rise"] or 0.0, max(nums))
                i = j + 2
                continue
            # 'name(args)' followed by something else: fall through as scalar-ish
            i = j + 1
            continue

        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t or "") and i + 1 < n and toks[i + 1] == ":":
            key = t
            j = i + 2
            while j < n and toks[j] != ";":
                j += 1
            raw = raw_between(i + 2, j)   # 原文取回，不经 token 重组
            kinds = [k for k, _ in stack]
            if key == "area" and cur_cell and kinds[-2:] == ["library", "cell"]:
                cells[cur_cell]["area"] = raw
            if key == "direction" and cur_cell:
                if raw == "input":
                    cells[cur_cell]["in"] += 1
                elif raw == "output":
                    cells[cur_cell]["out"] += 1
            if key == "cell_rise" and cur_cell and raw:
                nums = num_list(raw)
                if nums:
                    c = cells[cur_cell]
                    c["max_rise"] = max(c["max_rise"] or 0.0, max(nums))
            if key == "values" and cur_cell and "cell_rise" in kinds:
                nums = num_list(raw)
                if nums:
                    c = cells[cur_cell]
                    c["max_rise"] = max(c["max_rise"] or 0.0, max(nums))
            if not stack or kinds == ["library"]:
                if key in ("time_unit", "nom_voltage", "nom_temperature"):
                    meta[key] = raw
            i = j + 1
            continue

        if t == "}":
            if stack:
                kind, _ = stack.pop()
                if kind == "cell":
                    cur_cell = None
            i += 1
            continue
        i += 1

    total_timing = sum(c["timing"] for c in cells.values())
    print(f"library            = {library}")
    print(f"time_unit          = {meta.get('time_unit')}")
    print(f"nom_voltage        = {meta.get('nom_voltage')}")
    print(f"nom_temperature    = {meta.get('nom_temperature')}")
    print(f"cell_count         = {len(cells)}")
    print(f"timing_group_count = {total_timing} (sum over cells)")
    print()
    for name in CELLS:
        c = cells.get(name)
        if not c:
            print(f"{name}: ABSENT")
            continue
        mr = f"{c['max_rise']:.6f}" if c["max_rise"] is not None else "None"
        print(f"{name:<16} area={c['area']:<8} in={c['in']} out={c['out']} "
              f"tg={c['timing']} rise_tables={c['rise_tables']} max_cell_rise={mr}")


if __name__ == "__main__":
    main()
