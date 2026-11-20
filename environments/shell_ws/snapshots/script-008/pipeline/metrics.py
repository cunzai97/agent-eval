#!/usr/bin/env python3
"""pipeline 的分组口径：按 region 汇总电表读数。

口径（不要改动，历史报表都依赖它）：
  * region 为空、或 kwh 缺失/无法解析为数字/为负数的行，整行跳过；
  * devices 是该 region 下出现过的去重 device 数；
  * rows 是参与汇总的行数，total_kwh / avg_kwh 保留两位小数；
  * 输出按 region 字典序升序。
"""


def summarize(rows):
    buckets = {}
    for row in rows:
        region = (row.get("region") or "").strip()
        raw = (row.get("kwh") or "").strip()
        if not region or not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value < 0:
            continue
        entry = buckets.setdefault(region, {"devices": set(), "rows": 0, "total": 0.0})
        entry["devices"].add((row.get("device") or "").strip())
        entry["rows"] += 1
        entry["total"] += value
    out = []
    for region in sorted(buckets):
        entry = buckets[region]
        out.append([
            region,
            str(len(entry["devices"])),
            str(entry["rows"]),
            "%.2f" % entry["total"],
            "%.2f" % (entry["total"] / entry["rows"]),
        ])
    return out
