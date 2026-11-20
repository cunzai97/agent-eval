# 双缓冲投机解码流水线 · 规格（实现 specdec/pipeline.py）

`simulate(draft_tps, target_tps, k, rounds, accepted_trace) -> dict`
返回 `{"wall_dual": float, "wall_serial": float, "speedup": float, "generated_total": int}`。

- `draft_tps`、`target_tps`：草稿模型 / 目标模型的解码吞吐（token/秒，正数）。
- `k`：每轮草稿生成的 token 数（int ≥ 1）。
- `rounds`：投机轮数（int ≥ 1）。
- `accepted_trace`：长度 = rounds 的 list，`accepted_trace[i]` 是第 i 轮被目标模型接受的草稿 token 数（0..k）。

## 每轮成本

- 草稿成本 `d = k / draft_tps`（生成 k 个草稿 token）。
- 校验成本 `v = (k + 1) / target_tps`（校验 k 个草稿 + 1 个目标模型自生的 bonus token）。

## 两档墙钟

- **串行（无双缓冲）** `wall_serial = rounds * (d + v)`。
- **双缓冲**：草稿 i+1 与校验 i 并行（草稿、目标是两套独立资源）。
  墙钟 = 首轮草稿 `d` + 中间 `rounds-1` 个重叠段（每段 `max(d, v)`）+ 末轮校验 `v`：
  `wall_dual = d + (rounds - 1) * max(d, v) + v`。
  不得产生气泡：当 `d != v` 时 `wall_dual < wall_serial`。

## 其它

- `speedup = wall_serial / wall_dual`。
- `generated_total = sum(a + 1 for a in accepted_trace)`（每轮接受 a 个 + 1 个 bonus）。
