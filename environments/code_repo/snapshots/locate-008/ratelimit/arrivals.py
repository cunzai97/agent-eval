"""复盘场景的请求流：由固定种子重放，任何机器上结果一致。"""

from ratelimit import config

_LCG_A = 1103515245
_LCG_C = 12345
_LCG_M = 2147483648  # 2**31


def _next(state):
    return (state * _LCG_A + _LCG_C) % _LCG_M


def build_requests(total=config.REQUEST_TOTAL, seed=config.SCENARIO_SEED, tenants=config.TENANTS):
    """生成 total 个请求：到达时间严格递增，租户与成本权重由种子决定。"""
    state = seed % _LCG_M
    out = []
    clock = 0
    for i in range(total):
        state = _next(state)
        gap = ((state >> 16) % 7) + 1          # 相邻请求间隔 1..7 秒
        state = _next(state)
        tenant = tenants[((state >> 16) % len(tenants))]
        state = _next(state)
        weight = (state >> 16) % 3             # 0..2，折算为成本 1..3
        clock += gap
        out.append({"id": "r%03d" % i, "at": clock, "tenant": tenant, "weight": weight})
    return out
