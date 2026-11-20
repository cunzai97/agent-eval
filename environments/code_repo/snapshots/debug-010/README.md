# breadth —— 市场宽度扫描（new_high 250 日前高）

每日收盘后跑一遍全市场，输出"今日刷新 250 日前高"的标的清单。同一个指标有
**两条彼此独立的推导路径**，这是刻意设计的交叉校验，不是重复代码：

| 路径 | 模块 | 取数方式 | 用途 |
|---|---|---|---|
| 慢路径 | `breadth/naive.py` | `bars.full_series()` 整段历史 | 口径参考实现、复盘 |
| 快路径 | `breadth/fastscan.py` | `bars.stream()` 预取最近若干根 | 粗筛 + 发布出数 |

## 指标口径（唯一权威定义在 `breadth/window.py`）

对某标的的收盘价序列 `closes[0..n-1]`，报告日为 `n-1`：

* `LOOKBACK = 250`，回看窗口是报告日**之前**的 250 根，即 `closes[n-251 : n-1]`，
  **含最早那一根**（`n-251`），不含报告日自身；
* 命中（`True`）：`closes[n-1]` **严格大于**窗口最大值；等于最大值不算刷新（`False`）；
* 报告日之前的 bar 不足 250 根时返回 `None`，该标的本期不参与统计（新股/停牌）。

## 发布不变量（本仓库的硬约束）

```
naive.scan_all(symbols) == fastscan.scan_all(symbols)      # 逐 symbol 完全一致
report.publish()["consistent"] is True                     # 否则拒绝出数
```

2025-09 的两次事故正是因为这条不变量被破坏：快路径连续报出"全市场 0 只创新高"，
而慢路径同日报 4 只（见 `logs/`）。快路径与 `window.py` 的口径偏差不止一处——
**修的是快路径，慢路径与合成数据都是对的**，不要去改数据来"让两条路径一致"。

## 公开接口（不得改名、不得删参数、不得改变返回类型）

* `breadth.bars`：`SYMBOLS`、`BAR_LENGTHS`、`LOOKBACK`、`HISTORY_BARS`、
  `full_series(symbol)`、`stream(symbol, limit=HISTORY_BARS)`（返回按时间升序的 `Bar` 列表）
* `breadth.window`：`LOOKBACK`、`prior_window(closes, index, lookback)`、
  `new_high_at(closes, index, lookback)`
* `breadth.naive`：`scan_symbol(symbol)`、`scan_all(symbols=None)`、`hits(symbols=None)`
* `breadth.fastscan`：`NewHighTracker(lookback=LOOKBACK)`（`push(close)`、`feed(closes)`）、
  `scan_symbol(symbol)`、`scan_all(symbols=None)`、`hits(symbols=None)`
* `breadth.report`：`diff(symbols=None)`、`publish(symbols=None)`

## 工程约束

1. 快路径**必须**继续走 `bars.stream()` 预取（全市场 5000+ 标的，不允许每条都拉整段历史）；
   把快路径改成调用 `full_series`/慢路径不算修复，只是把交叉校验删掉了。
2. 增量实现的正确性判据只有一个：与 `window.new_high_at` 在任意输入下逐根一致。
3. `bars.py` 的序列是确定性的合成夹具，其中几个标的的尾部被刻意构造成对照形态
   （持平前高、高点恰在窗口最老一根、高点在窗口外、预热边界），不要动它。
4. `tests/` 只覆盖慢路径口径与形状断言，它们现在全绿，改完之后也必须全绿。
