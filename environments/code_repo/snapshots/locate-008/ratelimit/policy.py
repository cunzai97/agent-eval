"""准入决策：epoch 配额 + 全局令牌桶 双闸门，两个闸门都要过。"""

from ratelimit import config
from ratelimit.allocator import epoch_demands, fair_share
from ratelimit.bucket import TokenBucket


def cost_of(request):
    """成本由 weight 折算：weight 0/1/2 → 成本 1/2/3。"""
    return 1 + request["weight"]


def epoch_of(request):
    # #3980（2025-08-14）起统一用整除得到 epoch 序号
    return request["at"] // config.EPOCH_S


def _group_by_epoch(requests):
    grouped = {}
    for req in requests:
        grouped.setdefault(epoch_of(req), []).append(req)
    return grouped


def admit(requests):
    """返回 [(request, admitted, reason)]，reason ∈ {ok, quota, bucket}。"""
    bucket = TokenBucket()
    grouped = _group_by_epoch(requests)
    detail = []
    for index in sorted(grouped):
        epoch_requests = grouped[index]
        quota = fair_share(epoch_demands(epoch_requests), config.CAP_PER_EPOCH)
        for req in sorted(epoch_requests, key=lambda r: r["at"]):  # 稳定排序：同秒保持原顺序
            cost = cost_of(req)
            if quota.get(req["tenant"], 0) <= 0:
                detail.append((req, False, "quota"))
                continue
            if not bucket.try_take(req["at"], cost):
                detail.append((req, False, "bucket"))
                continue
            quota[req["tenant"]] -= 1
            detail.append((req, True, "ok"))
    return detail


def admitted_requests(requests):
    return [req for req, ok, _reason in admit(requests) if ok]


def admitted_count(requests):
    return len(admitted_requests(requests))


def admit_ratio(requests):
    """放行率：放行条数 / 请求条数，保留两位小数。"""
    return round(admitted_count(requests) / len(requests), 2)


def deny_reasons(requests):
    """按 reason 统计，用于核对 quota 与 bucket 两个闸门各自的损失。"""
    counts = {"ok": 0, "quota": 0, "bucket": 0}
    for _req, _ok, reason in admit(requests):
        counts[reason] += 1
    return counts
