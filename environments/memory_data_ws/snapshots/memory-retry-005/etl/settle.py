"""月末结算：读取 config/as_of 作为基准日，按修订去重后汇总。"""
import csv, os, sys
from decimal import Decimal

AS_OF = "config/as_of"
if not os.path.isfile(AS_OF):
    raise SystemExit(
        "ValueError: 缺少 config/as_of，无法确定结算基准日。"
        "基准日口径见 docs/runbook.md（当月最后一个结算工作日，参考 config/holidays.md）。"
    )
as_of = open(AS_OF, encoding="utf-8").read().strip()
if not as_of:
    raise SystemExit("ValueError: config/as_of 为空")

latest = {}
with open("data/positions.csv", encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        if row["status"] == "cancelled":
            continue
        if row["booked_date"] > as_of:
            continue
        seq = int(row["as_of_seq"])
        if row["trade_id"] not in latest or seq > int(latest[row["trade_id"]]["as_of_seq"]):
            latest[row["trade_id"]] = row

buckets = {}
for row in latest.values():
    d = row["booked_date"]
    net, cnt = buckets.get(d, (Decimal("0"), 0))
    buckets[d] = (net + Decimal(row["amount"]), cnt + 1)

os.makedirs("out", exist_ok=True)
with open("out/settlement.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["booked_date", "net_amount", "trade_count"])
    for d in sorted(buckets):
        net, cnt = buckets[d]
        w.writerow([d, f"{net:.2f}", cnt])
print(f"as_of={as_of} trades={len(latest)} rows={len(buckets)}")
