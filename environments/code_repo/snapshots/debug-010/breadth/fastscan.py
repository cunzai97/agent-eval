"""快路径：逐根推进的增量扫描，粗筛与发布都走这条。

必须与慢路径（breadth.naive）对每个 symbol 给出完全相同的判定，
见 README 的发布不变量。
"""
from __future__ import annotations

from collections import deque

from . import bars
from .window import LOOKBACK


class NewHighTracker:
    """滚动维护"最近 lookback 根前值"，每喂入一根收盘价返回该根的判定。

    返回值语义与 breadth.window.new_high_at 一致：前值不足 lookback 根时为 None。
    """

    def __init__(self, lookback=LOOKBACK):
        self.lookback = lookback
        self._prior = deque(maxlen=lookback)

    def push(self, close):
        if len(self._prior) < self.lookback:
            flag = None
        else:
            flag = close >= max(self._prior)
        self._prior.append(close)
        return flag

    def feed(self, closes):
        """批量喂入，返回最后一根的判定。"""
        flag = None
        for close in closes:
            flag = self.push(close)
        return flag


def scan_symbol(symbol):
    tracker = NewHighTracker()
    return tracker.feed([bar.close for bar in bars.stream(symbol)])


def scan_all(symbols=None):
    symbols = bars.SYMBOLS if symbols is None else tuple(symbols)
    return {symbol: scan_symbol(symbol) for symbol in sorted(symbols)}


def hits(symbols=None):
    return sorted(symbol for symbol, flag in scan_all(symbols).items() if flag)
