"""发布口径：new_high 的唯一权威定义。

慢路径（naive）直接调用这里；快路径（fastscan）必须是它的增量等价实现。
"""

LOOKBACK = 250          # 前高回看根数：报告日"之前"的 250 根收盘价


def prior_window(closes, index, lookback=LOOKBACK):
    """报告日 index 之前、最多 lookback 根收盘价（不含 index 自身）。"""
    return closes[max(0, index - lookback):index]


def new_high_at(closes, index, lookback=LOOKBACK):
    """报告日 index 是否刷新前高。

    * True  : closes[index] 严格大于此前 lookback 根的最大值（含最早那一根）；
    * False : 小于或**等于**该最大值（相等不算刷新）；
    * None  : index 之前的 bar 不足 lookback 根，该 symbol 本期不参与统计。
    """
    if index < lookback:
        return None
    return closes[index] > max(prior_window(closes, index, lookback))
