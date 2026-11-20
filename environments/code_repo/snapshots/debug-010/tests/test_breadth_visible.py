"""可见测试：慢路径口径、数据形状与接口契约。

它们现在全绿，修完之后也必须全绿——所以它们**不是**判据。真正的故障是快路径与
慢路径对同一份数据给出不一致的标记（README 的发布不变量），需要自己推导验证。
"""
import pytest

from breadth import bars, fastscan, naive, report, window

SLOW_EXPECTED = {
    "AERO": False,
    "CAPX": True,
    "ELEV": False,
    "GLNX": True,
    "HDIX": True,
    "MEDX": False,
    "MICRO": None,
    "NEWX": True,
    "RAIL": False,
    "SOFT": False,
}

SLOW_HITS = ["CAPX", "GLNX", "HDIX", "NEWX"]


def test_dataset_shape_is_stable():
    assert bars.SYMBOLS == ("CAPX", "GLNX", "MEDX", "ELEV", "HDIX",
                            "RAIL", "AERO", "SOFT", "NEWX", "MICRO")
    assert bars.BAR_LENGTHS["CAPX"] == 330
    assert bars.BAR_LENGTHS["NEWX"] == 251
    assert bars.BAR_LENGTHS["MICRO"] == 200
    for symbol, length in bars.BAR_LENGTHS.items():
        series = bars.full_series(symbol)
        assert len(series) == length
        assert [bar.date for bar in series] == sorted(bar.date for bar in series)


def test_full_series_is_deterministic():
    first = bars.full_series("CAPX")
    second = bars.full_series("CAPX")
    assert first == second
    assert all(isinstance(bar.close, float) for bar in first)


def test_stream_hands_back_the_most_recent_bars():
    series = bars.full_series("CAPX")
    assert bars.stream("CAPX", limit=5) == series[-5:]
    assert bars.stream("CAPX", limit=1) == [series[-1]]


def test_window_reference_semantics():
    rising = [round(10.0 + 0.1 * i, 3) for i in range(260)]
    assert window.new_high_at(rising, 259) is True
    assert window.new_high_at(rising, 100) is None            # 前值不足 250 根
    flat_top = rising[:-1] + [max(rising[:-1])]
    assert window.new_high_at(flat_top, len(flat_top) - 1) is False   # 持平不算刷新
    assert window.LOOKBACK == 250
    assert len(window.prior_window(rising, 259)) == 250


def test_naive_scan_matches_the_reference_expectations():
    assert naive.scan_all() == SLOW_EXPECTED
    assert naive.hits() == SLOW_HITS


def test_tracker_matches_window_on_a_strictly_increasing_run():
    tracker = fastscan.NewHighTracker(lookback=4)
    flags = [tracker.push(round(1.0 + i, 3)) for i in range(10)]
    assert flags == [None] * 4 + [True] * 6


def test_tracker_returns_none_until_the_window_is_full():
    fresh = fastscan.NewHighTracker(lookback=5)
    assert fresh.feed([float(i) for i in range(5)]) is None
    assert fresh.push(99.0) is True


def test_report_publishes_the_slow_path_reference():
    payload = report.publish()
    assert set(payload) >= {"as_of", "total_symbols", "slow", "fast", "unknown", "consistent"}
    assert payload["as_of"] == "2025-09-16"
    assert payload["total_symbols"] == 10
    assert payload["slow"] == SLOW_HITS
    assert payload["unknown"] == ["MICRO"]
    assert isinstance(payload["consistent"], bool)


def test_unknown_symbol_is_rejected():
    with pytest.raises(KeyError):
        bars.full_series("NOPE")
    with pytest.raises(KeyError):
        naive.scan_symbol("NOPE")
