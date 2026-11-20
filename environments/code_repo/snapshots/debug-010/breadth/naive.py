"""慢路径：整段历史全量扫描。发布口径直接由 breadth.window 提供。"""
from __future__ import annotations

from . import bars, window


def scan_symbol(symbol):
    """报告日（该 symbol 最后一根 bar）是否刷新 250 日前高：True / False / None。"""
    closes = [bar.close for bar in bars.full_series(symbol)]
    return window.new_high_at(closes, len(closes) - 1)


def scan_all(symbols=None):
    symbols = bars.SYMBOLS if symbols is None else tuple(symbols)
    return {symbol: scan_symbol(symbol) for symbol in sorted(symbols)}


def hits(symbols=None):
    return sorted(symbol for symbol, flag in scan_all(symbols).items() if flag)
