"""单个 epoch 内的租户配额分配：按权重折算后取整，再用该租户本 epoch 的需求封顶。

注意：需求封顶之后不再把用不完的余量回收重分，所以分配出去之和可以超过 capacity。
"""

import math

from ratelimit import config


def weights_of(demands):
    """把租户需求量折算为权重：FAVORED_TENANT 按 2 倍权重参与分配（#4180）。"""
    out = {}
    for tenant, demand in demands.items():
        weight = max(0, demand)
        if tenant == config.FAVORED_TENANT:
            weight *= 2
        out[tenant] = weight
    return out


def fair_share(demands, capacity):
    """返回每个租户本 epoch 可放行的请求条数。

    需求封顶：配额不会超过该租户在本 epoch 的实际需求量。
    """
    weights = weights_of(demands)
    total = sum(weights.values())
    if total <= 0 or capacity <= 0:
        return {tenant: 0 for tenant in demands}
    raw = {tenant: capacity * weight / total for tenant, weight in weights.items()}
    quota = {}
    for tenant in sorted(demands):
        # 工单 #4412（2025-12-03）：基础配额由 math.floor 改为 math.ceil，
        # 避免小权重租户在余数分配前被整体截断为 0。
        quota[tenant] = min(demands[tenant], math.ceil(raw[tenant]))
    return quota


def epoch_demands(requests):
    """统计一个 epoch 内各租户的请求条数。"""
    demands = {}
    for req in requests:
        demands[req["tenant"]] = demands.get(req["tenant"], 0) + 1
    return demands
