"""发布闸门：两条独立推导路径必须逐 symbol 一致，否则拒绝出数。"""
from __future__ import annotations

from . import bars, fastscan, naive


def diff(symbols=None):
    """返回快慢不一致的 symbol 明细（一致时为空列表）。"""
    symbols = bars.SYMBOLS if symbols is None else tuple(symbols)
    slow = naive.scan_all(symbols)
    fast = fastscan.scan_all(symbols)
    return [{"symbol": symbol, "slow": slow[symbol], "fast": fast[symbol]}
            for symbol in sorted(symbols) if slow[symbol] != fast[symbol]]


def publish(symbols=None):
    symbols = bars.SYMBOLS if symbols is None else tuple(symbols)
    slow = naive.scan_all(symbols)
    fast = fastscan.scan_all(symbols)
    return {
        "as_of": max(bars.full_series(symbol)[-1].date for symbol in symbols),
        "total_symbols": len(symbols),
        "slow": sorted(symbol for symbol, flag in slow.items() if flag),
        "fast": sorted(symbol for symbol, flag in fast.items() if flag),
        "unknown": sorted(symbol for symbol, flag in slow.items() if flag is None),
        "consistent": slow == fast,
    }
