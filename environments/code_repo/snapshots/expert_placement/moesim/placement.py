"""MoE 专家三级放置 + LRU 驱逐 + 成本记账。实现 place_and_replay，契约见 spec.md。"""

HOT = 3  # distinct 访问步数达到即视为热点专家


def place_and_replay(experts, capacities, read_cost, trace):
    """重放 trace，返回 total_cost / evictions / final_tiers。"""
    # TODO: 实现 rank/la/loc 推进、目标层判定、vram/ram 满时 LRU 驱逐与 demote
    raise NotImplementedError
