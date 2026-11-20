"""Hidden checks for snapshot debug-010.

The visible suite is a smoke suite: it pins the slow path, the data shape and the
public API, and it stays green both before and after the repair. The real fault is
that the incremental fast path disagrees with the slow path on the shipped data,
and the README turns that agreement into a publish blocker.

These checks carry the teeth. They compare the two paths symbol by symbol, pin the
window semantics (strictly greater; exactly 250 prior bars *including* the oldest
one; the warm-up boundary), pin that a stale record outside the window must not
suppress the flag (so widening the window to "all history" does not pass either),
and pin that the fast path still reads through the bounded prefetch (so delegating
to the slow path, or shrinking the window, does not pass).  The prefetch check pins
both halves of the depth defect: the stale module-level default `bars.HISTORY_BARS`
must itself stop being 80, and every symbol must actually be handed enough bars --
capped by how many bars that symbol has, since MICRO's series is only 200 long.
"""
from breadth import bars, fastscan, naive, report

LOOKBACK = 250
SLOW_HITS = ["CAPX", "GLNX", "HDIX", "NEWX"]
TIED = ["ELEV", "MEDX"]          # 报告日收盘恰好等于窗口前高
STALE = ["HDIX"]                 # 历史高点落在 250 根窗口之外
SHORT = ["MICRO"]                # 前值不足 250 根


def reference(closes, lookback=LOOKBACK):
    """独立于仓库实现的判定：报告日（最后一根）的 True/False/None。"""
    index = len(closes) - 1
    if index < lookback:
        return None
    return closes[index] > max(closes[index - lookback:index])


def tracker_result(closes, lookback=LOOKBACK):
    return fastscan.NewHighTracker(lookback).feed(closes)


def ramp(count, base=1.0, step=0.04):
    return [round(base + step * i, 3) for i in range(count)]


def test_fast_and_naive_agree_on_every_shipped_symbol():
    slow, fast = naive.scan_all(), fastscan.scan_all()
    assert set(slow) == set(bars.SYMBOLS) == set(fast)
    assert fast == slow, {s: (slow[s], fast[s]) for s in slow if slow[s] != fast[s]}
    assert report.diff() == []


def test_fast_path_finds_the_published_new_highs():
    assert fastscan.hits() == SLOW_HITS
    payload = report.publish()
    assert payload["consistent"] is True
    assert payload["slow"] == payload["fast"] == SLOW_HITS
    assert payload["unknown"] == SHORT


def test_a_tie_with_the_prior_high_is_not_a_new_high():
    for symbol in TIED:
        closes = [bar.close for bar in bars.full_series(symbol)]
        assert reference(closes) is False
        assert fastscan.scan_symbol(symbol) is False

    tracker = fastscan.NewHighTracker(lookback=5)
    assert tracker.feed([1.0, 2.0, 3.0, 4.0, 5.0]) is None
    assert tracker.push(5.0) is False          # 与前高严格持平，不算刷新
    assert tracker.push(5.001) is True


def test_window_holds_exactly_lookback_prior_bars():
    oldest_inside = ramp(LOOKBACK + 2)
    oldest_inside[1] = 50.0                    # 窗口最老那一根（index n-251）
    oldest_inside[-1] = 20.0
    assert reference(oldest_inside) is False   # 那根 50 必须还在窗口里
    assert tracker_result(oldest_inside) is False

    just_outside = ramp(LOOKBACK + 2)
    just_outside[0] = 60.0                     # 比窗口最老一根再早一根
    just_outside[-1] = 20.0
    assert reference(just_outside) is True     # 窗口外的高点压不住报告日
    assert tracker_result(just_outside) is True


def test_prefetch_depth_covers_the_full_lookback_window(monkeypatch):
    delivered = []
    real_stream = bars.stream

    def spy(symbol, *args, **kwargs):
        handed = list(real_stream(symbol, *args, **kwargs))
        delivered.append((symbol, len(handed)))
        return handed

    monkeypatch.setattr(bars, "stream", spy)
    assert fastscan.scan_all() == naive.scan_all()
    assert delivered, "快路径必须继续通过 bars.stream 预取，不得回落到整段历史"
    assert {symbol for symbol, _ in delivered} == set(bars.SYMBOLS)
    assert bars.HISTORY_BARS >= LOOKBACK + 1, \
        "预取默认值 bars.HISTORY_BARS 仍是旧粗筛留下的 80 根：任何按默认值取数的调用方都撑不起 250 根窗口"
    for symbol, count in delivered:
        # 序列本身短于窗口的标的（MICRO 只有 200 根）只能交出它拥有的那么多根
        needed = min(LOOKBACK + 1, bars.BAR_LENGTHS[symbol])
        assert count >= needed, f"{symbol} 只预取了 {count} 根，撑不起 250 根前值窗口"


def test_warm_up_boundary_and_short_history():
    assert fastscan.scan_symbol("MICRO") is None is naive.scan_symbol("MICRO")
    assert bars.BAR_LENGTHS["NEWX"] == LOOKBACK + 1
    assert fastscan.scan_symbol("NEWX") is True is naive.scan_symbol("NEWX")

    exactly_short = ramp(LOOKBACK)             # 250 根：只有 249 根前值
    assert reference(exactly_short) is None
    assert tracker_result(exactly_short) is None


def test_a_stale_record_outside_the_window_does_not_suppress_the_flag():
    for symbol in STALE:
        closes = [bar.close for bar in bars.full_series(symbol)]
        assert max(closes) > closes[-1] > max(closes[-LOOKBACK - 1:-1])
        assert naive.scan_symbol(symbol) is True
        assert fastscan.scan_symbol(symbol) is True


def test_incremental_tracker_matches_full_recalculation_case_by_case():
    spike_inside = ramp(300)
    spike_inside[49] = 999.0
    spike_inside[-1] = 20.0
    spike_outside = ramp(300)
    spike_outside[48] = 999.0
    spike_outside[-1] = 20.0
    plateau = ramp(260) + [max(ramp(260))] * 3
    cases = {
        "rising": ramp(400),
        "falling": ramp(400)[::-1],
        "flat": [25.0] * 400,
        "tie_at_report": ramp(330)[:-1] + [max(ramp(330)[:-1])],
        "spike_oldest_in_window": spike_inside,
        "spike_just_outside": spike_outside,
        "short_run": ramp(120),
        "boundary_251": ramp(LOOKBACK + 1),
        "sawtooth": [round(30.0 + 6.0 * (i % 7) - 0.01 * i, 3) for i in range(400)],
        "plateau": plateau,
    }
    assert len(cases) >= 6
    for name, closes in sorted(cases.items()):
        assert tracker_result(closes) == reference(closes), name
        pushy = fastscan.NewHighTracker(LOOKBACK)
        last = None
        for close in closes:
            last = pushy.push(close)
        assert last == reference(closes), name
