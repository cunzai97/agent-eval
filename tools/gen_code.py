"""生成 code_repo 快照（locate/fix/debug 各 4 个新任务），并校验每个的 expected fail 数。
每个 bug 都要真实存在（测试确实挂），保证任务可被确定性验证。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "environments/code_repo/snapshots")


def gen(name, files):
    d = os.path.join(ROOT, name)
    for rel, c in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(c)
    open(os.path.join(d, "pytest.ini"), "w").write("[pytest]\n")
    return d


def check(d, expect_fail):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-o", "addopts=", "--no-header", "-p", "no:cacheprovider"],
        cwd=d, capture_output=True, text=True, timeout=120,
    )
    out = r.stdout + r.stderr
    g = lambda pat: (re.search(pat, out).group(1) if re.search(pat, out) else "0")
    p, f, e = g(r"(\d+) passed"), g(r"(\d+) failed"), g(r"(\d+) error")
    ok = int(f) == expect_fail and int(e) == 0
    print(f"  {'OK ' if ok else 'BAD'} {os.path.basename(d)}: {p}p {f}f {e}e (expect fail={expect_fail})")
    return ok


# ============ locate（定位根因文件，answer 提交）============

gen("locate-002", {
    "billing/base.py": "def base_total(lines):\n    return sum(l['amount'] for l in lines)\n",
    "billing/fees.py": "SERVICE_FEE = 20\n\ndef add_fee(t):\n    return t + SERVICE_FEE\n",
    "billing/final.py": "from .base import base_total\nfrom .fees import add_fee\n\n\ndef order_final(lines):\n    t = base_total(lines)\n    t = add_fee(t)\n    t = add_fee(t)\n    return t\n",
    "tests/test_final.py": "from billing.final import order_final\n\n\ndef test_full():\n    assert order_final([{'amount': 100}]) == 120\n\n\ndef test_half():\n    assert order_final([{'amount': 50}]) == 70\n",
})

gen("locate-003", {
    "calc2/stats.py": "def average(vals):\n    return sum(vals) / len(vals)\n",
    "calc2/ops.py": "def safe_div(a, b):\n    return a / b if b else 0.0\n",
    "calc2/service.py": "from .stats import average\n\n\ndef process(recs):\n    return [average(r) for r in recs]\n",
    "tests/test_service.py": "from calc2.service import process\n\n\ndef test_ok():\n    assert process([[1, 2, 3]]) == [2.0]\n\n\ndef test_empty():\n    assert process([[], [4, 6]]) == [0.0, 5.0]\n",
})

gen("locate-004", {
    "geo/dist.py": "def dist(a, b):\n    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5\n",
    "geo/nearest.py": "from .dist import dist\n\n\ndef nearest(p, pts):\n    best = None\n    bd = 0.0\n    for q in pts:\n        d = dist(p, q)\n        if d < bd:\n            bd = d\n            best = q\n    return best\n",
    "tests/test_nearest.py": "from geo.nearest import nearest\n\n\ndef test_nn():\n    assert nearest((0, 0), [(1, 0), (5, 0)]) == (1, 0)\n",
})

gen("locate-005", {
    "mem/acc.py": "def add_item(items, bucket=[]):\n    bucket.append(items)\n    return bucket\n",
    "tests/test_acc.py": "from mem.acc import add_item\n\n\ndef test1():\n    assert add_item(['a']) == [['a']]\n\n\ndef test2():\n    assert add_item(['b']) == [['b']]\n",
})

# ============ fix-and-test（修到全绿，测试文件不得改）============

gen("fix-002", {
    "money/convert.py": "def to_cents(dollars):\n    return int(dollars * 100)\n",
    "tests/test_convert.py": "from money.convert import to_cents\n\n\ndef test_a():\n    assert to_cents(19.99) == 1999\n\n\ndef test_b():\n    assert to_cents(0.29) == 29\n\n\ndef test_c():\n    assert to_cents(1.0) == 100\n",
})

gen("fix-003", {
    "text/slug.py": "def slugify(s):\n    return s.lower().replace(' ', '-')\n",
    "tests/test_slug.py": "from text.slug import slugify\n\n\ndef test_a():\n    assert slugify('Hello World') == 'hello-world'\n\n\ndef test_b():\n    assert slugify('a_b') == 'a-b'\n\n\ndef test_c():\n    assert slugify('A B') == 'a-b'\n",
})

gen("fix-004", {
    "api/parse.py": "def parse(s):\n    return [x for x in s.split(',')]\n",
    "api/summarize.py": "from .parse import parse\n\n\ndef summarize(s):\n    vals = parse(s)\n    return sum(vals)\n",
    "tests/test_api.py": "from api.summarize import summarize\n\n\ndef test_a():\n    assert summarize('1,2,3') == 6\n",
})

gen("fix-005", {
    "cfg/merge.py": "def merge(base, override):\n    m = dict(override)\n    m.update(base)\n    return m\n",
    "tests/test_cfg.py": "from cfg.merge import merge\n\n\ndef test_a():\n    assert merge({'a': 1}, {'b': 2}) == {'a': 1, 'b': 2}\n\n\ndef test_b():\n    assert merge({'a': 1}, {'a': 9}) == {'a': 9}\n\n\ndef test_c():\n    assert merge({'a': 1}, {}) == {'a': 1}\n",
})

# ============ debug（给现象/栈，找根因并修复）============

gen("debug-002", {
    "table/grid.py": "def row_sum(grid, r):\n    return sum(grid[r])\n",
    "table/col.py": "def col_len(grid):\n    return len(grid[0])\n",
    "tests/test_grid.py": "from table.grid import row_sum\n\n\ndef test_a():\n    assert row_sum([[1, 2], [3, 4]], 0) == 3\n\n\ndef test_b():\n    assert row_sum([], 0) == 0\n",
})

gen("debug-003", {
    "etl/read.py": "DB = {'ok': [1, 2]}\n\n\ndef read(path):\n    return None\n",
    "etl/transform.py": "from .read import read\n\n\ndef run(path):\n    data = read(path)\n    return [x * 2 for x in data]\n",
    "tests/test_etl.py": "from etl.transform import run\n\n\ndef test_a():\n    assert run('ok') == [2, 4]\n",
})

gen("debug-004", {
    "agg/rate.py": "def success_rate(events):\n    n = len(events)\n    ok = sum(1 for e in events if e['ok'])\n    return ok / n if n else 0.0\n",
    "agg/report.py": "from .rate import success_rate\n\n\ndef report(events):\n    return {'rate': round(success_rate(events) * 10, 1)}\n",
    "tests/test_agg.py": "from agg.report import report\n\n\ndef test_a():\n    assert report([{'ok': True}, {'ok': False}]) == {'rate': 50.0}\n\n\ndef test_b():\n    assert report([{'ok': True}]) == {'rate': 100.0}\n",
})

gen("debug-005", {
    "funcs/make.py": "def make_adders(nums):\n    adders = []\n    for n in nums:\n        adders.append(lambda: n + 1)\n    return adders\n",
    "tests/test_make.py": "from funcs.make import make_adders\n\n\ndef test_a():\n    adds = make_adders([1, 2, 3])\n    assert [f() for f in adds] == [2, 3, 4]\n",
})

# ---- 校验 ----
if __name__ == "__main__":
    cases = [
        ("locate-002", 2), ("locate-003", 1), ("locate-004", 1), ("locate-005", 1),
        ("fix-002", 2), ("fix-003", 1), ("fix-004", 1), ("fix-005", 1),
        ("debug-002", 1), ("debug-003", 1), ("debug-004", 2), ("debug-005", 1),
    ]
    print("== 校验快照 expected fail 数 ==")
    bad = 0
    for name, exp in cases:
        ok = check(os.path.join(ROOT, name), exp)
        bad += 0 if ok else 1
    print(f"== 完成，{bad} 个异常 ==")
