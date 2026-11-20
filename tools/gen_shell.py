"""生成 shell_ws 快照（script-dev 4 个新任务）的输入文件。"""
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "environments/shell_ws/snapshots")


def gen(name, files):
    d = os.path.join(ROOT, name)
    for rel, c in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(c)
    print("gen", name)


# script-002: 词频统计
gen("script-002", {
    "words.txt":
        "apple banana apple\n"
        "cherry apple banana\n"
        "cherry cherry\n",
})

# script-003: 多文件汇总报告（a sum=6, b sum=30, grand=36）
gen("script-003", {
    "a.csv": "v\n1\n2\n3\n",
    "b.csv": "v\n10\n20\n",
})

# script-004: argparse 命令行（sum=30 count=3 avg=10）
gen("script-004", {
    "nums.csv": "v\n5\n10\n15\n",
})

# script-005: 解析日志统计 level（ERROR 2, INFO 2, WARN 1）
gen("script-005", {
    "app.log":
        "2025-01-01 ERROR db down\n"
        "2025-01-01 INFO ok\n"
        "2025-01-02 WARN slow\n"
        "2025-01-02 ERROR timeout\n"
        "2025-01-03 INFO ok\n",
})
