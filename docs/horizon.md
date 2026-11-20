# horizon：只换掉最长的那批题

日期：2026-10-28　题库：`banks/horizon.yaml`　统计明细：`runs/hard40_stats_2026-10-28.json`

新卷 17 题 = 5 道新增中长链路题 + 12 道历史中等耗时、有区分度的题。`hard40` 与全部历史结果保持原样，两卷可并行对照。

## 1. 统计口径

- 数据源 `runs/_HISTORY.jsonl`（85 次 run），只取 `graded` 尝试。
- 同一模型同一题有多次尝试时，只保留**最近一次**，避免反复刷题放大权重。
- 耗时取该次 `result.json` 的 `efficiency.elapsed_s`；缺失的不计入耗时统计。
- 有效样本：40 题、1525 个 model-task 对。

## 2. hard40 里最耗时的题

| 题目 | n | 通过率 | 中位耗时 | p75 | 处理 |
|---|---:|---:|---:|---:|---|
| fix-cancel-001 | 7 | 0.0% | 520s | 849s | 移出（无区分度） |
| debug-stacktrace-010 | 25 | 68.0% | 477s | 615s | 移出（过长） |
| script-loopguard-001 | 6 | 0.0% | 453s | 612s | 移出（无区分度） |
| debug-stacktrace-008 | 54 | 5.6% | 432s | 715s | 移出（墙题） |
| script-dev-007 | 24 | 75.0% | 378s | 522s | 移出（过长） |
| liberty-extract-001 | 55 | 7.3% | 353s | 729s | 移出（墙题） |
| fix-failing-test-009 | 24 | 79.2% | 342s | 507s | 移出（过长） |
| script-dev-005 | 53 | 81.1% | 286s | 396s | 保留 |
| fix-failing-test-008 | 53 | 11.3% | 260s | 424s | 移出（墙题） |
| fix-failing-test-004 | 53 | 92.5% | 261s | 328s | 未入选 |

估算总耗时 75.7 小时（中位耗时 × 次数）。最长的 3 题（477s/378s/342s）合计约 5.2 小时且全部通过率在 68%–79%，问题不在难度，而在单位时间产出的信息太少。

三类问题题：

- **高耗时低区分度**：fix-cancel-001（0/7，520s）、script-loopguard-001（0/6，453s）、debug-stacktrace-008（3/54，432s）、liberty-extract-001（4/55，353s），四题占历史总耗时 18.0%。
- **墙题**：fix-failing-test-008 6/53（11.3%），通过的 5 个模型全是 27B 级量化模型，luna-6、luna-max、deepseek-v4-flash 全灭；这类题接近抽奖。
- **零区分度**：fix-cap-001 8/8、debug-stacktrace-009 25/25 全过；7 道题依赖 rubric 裁判。

## 3. 换进来的 5 道题

均只改 2–3 个源文件、跨模块调用链、确定性判分、无 rubric，预算 40 步 / 64k tok / 720s。

| 题目 | 考察链路 | 改动面 | 隐藏断言 |
|---|---|---|---:|
| fix-stream-window-001 | 乱序事件归一化 → 固定窗口 → 聚合 | `streamflow/{normalize,windows,aggregate}.py` | 34 |
| fix-retry-scheduler-001 | 退避策略 → 调度状态机 → stale callback 丢弃 | `retrykit/{policy,scheduler}.py` | 51 |
| fix-config-stack-001 | 分层合并 → 占位符插值 → 加载顺序 | `configstack/{merge,interpolate,loader}.py` | 36 |
| fix-dep-graph-001 | 语义化版本比较 → 约束选版 → 依赖拓扑排序 | `depkit/{version,resolve}.py` | 64 |
| fix-migration-flow-001 | 迁移发现排序 → 待执行计划 → 幂等应用与失败中止 | `migratekit/{registry,runner}.py` | 34 |

设计要点：

- **多阶段真实实现**：先跑可见测试复现，再沿调用链定位；单点修补过不了隐藏断言。
- **边界即契约**：`bool` 不算 `int`、prerelease 数字段按数值比较、迁移版本按整数排序、`None` 表示删除、默认值不递归展开等，全部写进题面并由隐藏测试验证。
- **反硬编码**：隐藏用例参数与可见用例不同；负控证明「可见全绿」不等于通过。
- 评分只用 `state_assert`（测试报告 + 隐藏全过 + 改动文件约束）和 `order_of`（先改后测）。

## 4. 验证结果

`tools/audit_invariants.py` 重放参考解两遍，5 题全部 `SOLVABLE+STABLE`，状态摘要一致、无失败调用：

| 题目 | 隐藏断言 | 可重复 |
|---|---:|---|
| fix-stream-window-001 | 34/34 | 是 |
| fix-retry-scheduler-001 | 51/51 | 是 |
| fix-config-stack-001 | 36/36 | 是 |
| fix-dep-graph-001 | 64/64 | 是 |
| fix-migration-flow-001 | 34/34 | 是 |

变异负控共 15 个：9 个针对早先三题（`NC-new-horizon-mutants.py`），6 个针对本次两题（`NC-horizon-medium-mutants.py`）。全部「可见测试全绿、隐藏测试变红」。

| 题目 | 变异 | 结果 |
|---|---|---|
| dep | 接受 build metadata（`1.2.3+meta`） | 被捕获 |
| dep | 丢弃未在 deps 中声明的叶子依赖 | 被捕获 |
| dep | 非字符串版本号不抛 ValueError | 被捕获 |
| migration | 保留目录前缀 | 被捕获 |
| migration | 按文件名字符串排序（10 排在 2 前） | 被捕获 |
| migration | 接受大写迁移名 | 被捕获 |

证据：`runs/horizon_invariants.json`（五题统一重放）、`runs/new_horizon_invariants.json`、`runs/horizon_new_invariants.json`、`docs/hardening_evidence/references/`。

## 5. 新卷组成

| 类别 | 题数 | 说明 |
|---|---:|---|
| 新增中长链路题 | 5 | 见上表，无 rubric |
| 历史中等耗时题 | 12 | 通过率 42.6%–88.9%，中位 45.9–286.3s |
| 合计 | 17 | 单题中位耗时上限 286s（新题待建基线） |

移出项：最长 3 题（debug-stacktrace-010、script-dev-007、fix-failing-test-009）；高耗时低区分度 4 题；零区分度 2 题；墙题 3 题；裁判依赖 7 题。

## 6. 使用

```bash
python3 cli.py banks
python3 cli.py run --bank horizon --models <model>
python3 tools/bank_board.py --bank horizon
```

## 7. 待办

- 5 道新题尚无模型实跑样本；首轮结束后把通过率与中位耗时补进 `banks/horizon.yaml` 的 `detail:`。
- 若新题实测中位耗时明显超过 300s，优先缩减题面分支而不是放宽判分；若通过率 >90% 或 <20%，按同一口径替换。
