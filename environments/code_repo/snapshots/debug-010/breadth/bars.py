"""确定性日线序列：两条扫描路径的数据源。

* ``full_series(symbol)``            —— 整段历史，慢路径用它；
* ``stream(symbol, limit=...)``      —— 只回传最近 ``limit`` 根，快路径的预取接口。

序列是纯确定性合成（固定种子 + 角色化尾部），因此每次调用完全一致。
"""
from __future__ import annotations

import random
from collections import namedtuple
from datetime import date, timedelta

from .window import LOOKBACK

Bar = namedtuple("Bar", "date close")

HISTORY_BARS = 80       # 快路径单次预取的根数（旧粗筛留下的默认值）

LAST_TRADING_DAY = date(2025, 9, 16)

# 角色 -> (总根数, 距最后一个交易日的停牌天数)
ROLES = {
    "CAPX": ("record", 330, 0),
    "GLNX": ("record", 330, 0),
    "MEDX": ("tie", 330, 0),
    "ELEV": ("tie", 330, 0),
    "HDIX": ("stale_record", 330, 0),
    "RAIL": ("edge_of_window", 330, 0),
    "AERO": ("below", 330, 0),
    "SOFT": ("below", 330, 0),
    "NEWX": ("record_first_day", 251, 0),
    "MICRO": ("short", 200, 40),
}

SYMBOLS = tuple(ROLES)
BAR_LENGTHS = {symbol: length for symbol, (_, length, _) in ROLES.items()}


def _trading_days(count, end=LAST_TRADING_DAY):
    days, cursor = [], end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _walk(symbol, count):
    rng = random.Random(f"breadth/{symbol}")
    level = rng.uniform(12.0, 68.0)
    drift = rng.uniform(-0.0003, 0.0007)
    closes = []
    for _ in range(count):
        level = max(2.0, level * (1 + drift + rng.gauss(0.0, 0.017)))
        closes.append(round(level, 3))
    return closes


def _shape(symbol, role, closes):
    """把尾部改造成指定的对照形态，其余保持随机游走原样。"""
    count = len(closes)
    window_start = max(0, count - 1 - LOOKBACK)
    if role == "record":
        closes[-1] = round(max(closes[:-1]) * 1.021, 3)
    elif role == "tie":
        closes[-1] = max(closes[window_start:-1])            # 与前高完全持平
    elif role == "stale_record":
        spike_at = count - 1 - LOOKBACK - 1                  # 落在 250 根窗口之外
        body = max(closes[spike_at + 1:-1])
        closes[spike_at] = round(body * 1.42, 3)
        closes[-1] = round(body * 1.008, 3)
    elif role == "edge_of_window":
        spike_at = count - 1 - LOOKBACK                      # 恰好是窗口里最老那一根
        body = max(closes[spike_at + 1:-1])
        closes[spike_at] = round(body * 1.31, 3)
        closes[-1] = round(body * 1.004, 3)
    elif role == "record_first_day":
        closes[-1] = round(max(closes[:-1]) + 0.42, 3)
    elif role == "below":
        closes[-1] = round(max(closes[window_start:-1]) * 0.93, 3)
    return closes


def _build(symbol):
    role, count, gap = ROLES[symbol]
    # gap>0 表示该标的在报告日前 gap 个交易日就停牌了，序列整体前移。
    days = _trading_days(count + gap)[:count]
    closes = _shape(symbol, role, _walk(symbol, count))
    return [Bar(day.isoformat(), close) for day, close in zip(days, closes)]


_CACHE: dict[str, list[Bar]] = {}


def full_series(symbol):
    """整段历史（按时间升序）。未知 symbol 抛 KeyError。"""
    if symbol not in ROLES:
        raise KeyError(f"unknown symbol: {symbol}")
    if symbol not in _CACHE:
        _CACHE[symbol] = _build(symbol)
    return list(_CACHE[symbol])


def stream(symbol, limit=HISTORY_BARS):
    """预取接口：只回传最近 ``limit`` 根，避免整段历史进内存。"""
    series = full_series(symbol)
    if not limit or limit >= len(series):
        return list(series)
    return series[-limit:]
