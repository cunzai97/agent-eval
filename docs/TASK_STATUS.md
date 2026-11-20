# 50 题现状：谁被改过、被怎么改的、谁没动

本文件由 `python3 tools/task_status.py` 生成，参照系是基线提交 `ea176cb`（初始化 40 题）。

- **新建 10 道**：data-pipeline-007、data-pipeline-008、data-pipeline-009、debug-stacktrace-009、debug-stacktrace-010、fix-failing-test-009、locate-bug-008、memory-loop-005、script-dev-007、script-dev-008
- **题面一字未动 2 道**：data-pipeline-004、data-pipeline-006（快照与隐藏判据同样未动）
- 其余 **38 道**题面被改过。判定加严的主线是 post-run 隐藏用例：**现在 27/50 道题归它管，基线只有 9/40**——其中 13 道是老题新加上来的（debug-stacktrace-001、debug-stacktrace-002、debug-stacktrace-003、debug-stacktrace-004、debug-stacktrace-005、debug-stacktrace-006、debug-stacktrace-007、fix-failing-test-001、fix-failing-test-002、fix-failing-test-003、fix-failing-test-004、fix-failing-test-005、fix-failing-test-006），9 道基线就有、只是用例内容被重做，5 道新建时就自带（debug-stacktrace-009、debug-stacktrace-010、fix-failing-test-009、script-dev-007、script-dev-008）。
- 裁判现在只管 7 道题：data-pipeline-007、data-pipeline-008、data-pipeline-009、memory-loop-001、memory-loop-002、memory-loop-003、memory-loop-004

| 题 | 类别 | 难度 | 相对基线的改动 |
|---|---|---|---|
| clean-aggregate-001 | data-pipeline | medium | 改判 medium |
| clean-aggregate-002 | data-pipeline | medium | 改判 medium |
| data-pipeline-003 | data-pipeline | medium | 改判 medium |
| data-pipeline-004 | data-pipeline | hard | 未动 |
| data-pipeline-005 | data-pipeline | medium | 改判 medium |
| data-pipeline-006 | data-pipeline | hard | 未动 |
| data-pipeline-007 | data-pipeline | hard | 新建 |
| data-pipeline-008 | data-pipeline | hard | 新建 |
| data-pipeline-009 | data-pipeline | hard | 新建 |
| debug-stacktrace-001 | debug | hard | 题面改、加隐藏用例、过程计数放宽、判据 +1 |
| debug-stacktrace-002 | debug | hard | 题面改、加隐藏用例、过程计数放宽、判据 +1 |
| debug-stacktrace-003 | debug | hard | 题面改、加隐藏用例、过程计数放宽、判据 +1 |
| debug-stacktrace-004 | debug | hard | 题面改、加隐藏用例、过程计数放宽、判据 +1 |
| debug-stacktrace-005 | debug | hard | 加隐藏用例、过程计数放宽、判据 +1 |
| debug-stacktrace-006 | debug | hard | 加隐藏用例 |
| debug-stacktrace-007 | debug | hard | 加隐藏用例、过程计数放宽、判据 +2 |
| debug-stacktrace-008 | debug | hard | 过程计数放宽、判据 +2 |
| debug-stacktrace-009 | debug | hard | 新建 |
| debug-stacktrace-010 | debug | hard | 新建 |
| fix-failing-test-001 | fix-and-test | hard | 加隐藏用例、过程计数放宽、判据 +1 |
| fix-failing-test-002 | fix-and-test | hard | 加隐藏用例、过程计数放宽、判据 +1 |
| fix-failing-test-003 | fix-and-test | hard | 题面改、加隐藏用例、过程计数放宽、判据 +1 |
| fix-failing-test-004 | fix-and-test | hard | 题面改、加隐藏用例、过程计数放宽、判据 +1 |
| fix-failing-test-005 | fix-and-test | hard | 加隐藏用例、过程计数放宽、判据 +1 |
| fix-failing-test-006 | fix-and-test | hard | 加隐藏用例 |
| fix-failing-test-007 | fix-and-test | hard | 过程计数放宽、判据 +2 |
| fix-failing-test-008 | fix-and-test | hard | 过程计数放宽、判据 +2 |
| fix-failing-test-009 | fix-and-test | hard | 新建 |
| liberty-extract-001 | data-pipeline | hard | 预算、过程计数放宽、判据 +1 |
| locate-bug-001 | locate | hard | 删 rubric、过程计数放宽、判据 +1 |
| locate-bug-002 | locate | hard | 提示词改、题面改、删 rubric、过程计数放宽、答案键改、判据 +4 |
| locate-bug-003 | locate | hard | 提示词改、题面改、删 rubric、过程计数放宽、答案键改、判据 +4 |
| locate-bug-004 | locate | hard | 提示词改、题面改、删 rubric、过程计数放宽、答案键改、判据 +4 |
| locate-bug-005 | locate | hard | 提示词改、题面改、删 rubric、过程计数放宽、答案键改、判据 +4 |
| locate-bug-006 | locate | hard | 提示词改、题面改、删 rubric、过程计数放宽、答案键改、判据 +4 |
| locate-bug-007 | locate | hard | 删 rubric、过程计数放宽、判据 +1 |
| locate-bug-008 | locate | hard | 新建 |
| memory-loop-001 | memory-loop | hard | 提示词改、钉 judge_id |
| memory-loop-002 | memory-loop | hard | 提示词改、钉 judge_id、过程计数放宽、判据 +1 |
| memory-loop-003 | memory-loop | hard | 提示词改、钉 judge_id |
| memory-loop-004 | memory-loop | hard | 提示词改、钉 judge_id、判据 +1、判据 -1 |
| memory-loop-005 | memory-loop | hard | 新建 |
| script-dev-002 | script-dev | hard | 题面改、隐藏用例判据改、过程计数放宽、判据 +1、判据 -1 |
| script-dev-003 | script-dev | hard | 提示词改、题面改、预算、隐藏用例判据改、判据 +2、判据 -3 |
| script-dev-004 | script-dev | hard | 提示词改、题面改、隐藏用例判据改、判据 +1、判据 -2 |
| script-dev-005 | script-dev | hard | 提示词改、题面改、预算、隐藏用例判据改、判据 +2、判据 -3 |
| script-dev-006 | script-dev | hard | 题面改、隐藏用例判据改、过程计数放宽、判据 +1、判据 -1 |
| script-dev-007 | script-dev | hard | 新建 |
| script-dev-008 | script-dev | hard | 新建 |
| write-cli-001 | script-dev | hard | 提示词改、题面改、预算、隐藏用例判据改、判据 +1、判据 -2 |

标签计数：过程计数放宽 25、判据 +1 19、题面改 17、加隐藏用例 13、提示词改 13、新建 10、删 rubric 7、判据 +2 6、隐藏用例判据改 6、答案键改 5、判据 +4 5、改判 medium 4、预算 4、钉 judge_id 4、判据 -1 3、未动 2、判据 -3 2、判据 -2 2

## 三处框架级改动不在题面 diff 里，但对 50 题全部生效

1. **重试与「未评」**（`fc7b8fa`）：上游连不上/5xx/断连/读超时按指数退避重试，且重试不吃本题
   剩余墙钟；用尽后 `termination=infra_error` → `graded=False` → 不进通过率分母、单独计数。
   跑批前探上游，不可达直接返回 2 不建 run。
2. **过程计数降级**（§10.9）：`min_calls`、`files_read >= N`、`grep_calls >= 2`、三步 `order_of`
   不再当能力代理；防假对整体交给隐藏用例与钉死的产物数值。
3. **沙箱与工具语义**：解释器从浮动标签改成 digest 钉定、铺环境即清字节码、`grep` 打目录不再
   静默返回空、`read_file` 明确 12,000 字符截断契约——这几条改的是环境层，题面看不出来。

## 两个要知道的耦合

- `locate-bug-001` 与 `fix-failing-test-001` 共用 `calc_buggy` 快照：给后者加 32 条隐藏用例时，
  前者的判定也一起变严（它不读 `state.hidden_tests`，结论不变，代价是收尾多跑 0.4 秒、指纹换掉）。
- `debug-stacktrace-006` 的题面已经引用 `state.hidden_tests`，而 `hidden_tests/debug-006.py` 目前
  **未入库**（另一会话在改这题）。那份文件一旦丢了，这道题会变成永远判不对——本轮审计跑成 50/50
  只是因为文件此刻在本地。这是别人手上的活，但依赖要记下。
