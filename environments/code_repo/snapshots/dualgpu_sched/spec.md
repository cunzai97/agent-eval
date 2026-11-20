# 双卡推理流水线调度 · 规格（实现 sched/scheduler.py）

`schedule(devices, requests) -> dict`，返回 `{"makespan": float, "completion": {req_id: float}}`。

- `devices`：恰好 2 个 dict，键 `name`、`prefill_tps`、`decode_tps`（token/秒，均为正数）。
- `requests`：list，每项 `id`、`prompt_len`（int）、`completion_len`（int）。

## 两阶段语义

**阶段一 · prefill（先 prefill 不 decode）**
- 每个请求的 prefill 整体指派到且仅到一张卡（不拆单请求）。一张卡可顺序处理多个请求的 prefill。
- 指派目标：最小化 prefill 阶段makespan = 两卡中"该卡 prompt_len 之和 ÷ 该卡 prefill_tps"的较大者。
- 平分时取让字典序较小的 `(卡A分配, 卡B分配)` 方案（按请求输入顺序比较指派序列）。
- prefill 阶段结束前，任何 decode 不得开始。

**阶段二 · decode（单卡）**
- 所有 decode 顺序跑在 `decode_tps` 更大的那张卡上（同速取先出现的卡）。
- decode 顺序 = 请求输入顺序。
- 请求 r 的完成时刻 = prefill_makespan + (含 r 在内的累计 completion_len ÷ 该 decode_tps)。

## 输出

- `makespan` = 最后一个请求的完成时刻。
- `completion` 每个请求一个完成时刻（float）。
