# MoE 专家三级放置与驱逐 · 规格（实现 moesim/placement.py）

`place_and_replay(experts, capacities, read_cost, trace) -> dict`
返回 `{"total_cost": float, "evictions": [(step, name, from_tier, to_tier), ...], "final_tiers": {name: tier}}`。

- `experts`：{name: size_bytes}。`capacities`：{"vram_bytes","ram_bytes"}，disk 无限。
- `read_cost`：{"vram","ram","disk"} 每次访问按**落地层**计一次。
- `trace`：list，每步是被访问专家名的 list（可能多个 = MoE4All 批内共访）。

## 语义（严格按步推进）

- 每位专家维护 distinct 访问步数 `rank`（含当前步）、最近访问步 `la`、当前层 `loc`（未加载=None）。
- 访问某专家 e 于步 s：`rank += 1`（若 s 未计入），`la = s`。
  - 目标层：`rank >= HOT` → vram；`rank >= 1` → ram；否则 disk。
  - 目标层等于当前层且已在该层 → 不动，计一次 `read_cost[层]`。
  - 否则把 e 搬到目标层：
    - 目标层是 vram 且满：驱逐 vram 里 `la` 最小者（并列取名字字典序小者），被驱逐者 demote 到 ram（ram 装得下）否则 disk。
    - 目标层是 ram 且满：驱逐 ram 里 `la` 最小者（并列字典序小者），被驱逐者直接到 disk。
    - e 若原在更高层，搬走后腾出该层空间。
  - 计一次 `read_cost[e 落地层]`。
- `HOT = 3`（写死常量，见 placement.py 顶部）。
- evictions 按发生顺序记录 `(step, 被驱逐者, 从, 到)`。
