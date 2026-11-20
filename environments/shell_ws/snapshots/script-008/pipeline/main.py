#!/usr/bin/env python3
"""日度电表读数汇总：读 data/in.csv，写 out/report.csv。

注意：两个路径都是**相对当前工作目录**解析的，所以本脚本必须先切到
pipeline/ 目录里再执行（见仓库根目录的 run.sh）。在其它目录下执行会
直接 FileNotFoundError。
"""
import csv
import os

from metrics import summarize

INPUT = os.path.join("data", "in.csv")
OUTPUT = os.path.join("out", "report.csv")


def load(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main():
    rows = load(INPUT)
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["region", "devices", "rows", "total_kwh", "avg_kwh"])
        for aggregate in summarize(rows):
            writer.writerow(aggregate)
    print("ok read=%d" % len(rows))


if __name__ == "__main__":
    main()
