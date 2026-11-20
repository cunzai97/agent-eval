# 评测报告

- run: `runs/deepseek-v4-flash-0921`
- models: deepseek-v4-flash
- tasks: 50（其中未评 0：上游故障或 rubric 没评成，不计入通过率分母）
- judge: luna-max:gpt-5.6-luna
- ⚠ 被测端点是**外部 API**：deepseek-v4-flash → https://api.deepseek.com/v1（wire=chat，密钥来源 env:DEEPSEEK_API_KEY）。这一行与本地量化端点不同尺，横向排序时按来源分层看。
- accounted_tok/s = 累计输入+输出 token / 任务墙钟时间；用于本评测内相对比较，不等同于纯生成速度。

## 总分、速度与消耗

| model | passed | graded | ungraded | rate | avg_steps | avg_tokens | avg_elapsed_s | accounted_tok/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| deepseek-v4-flash | 46 | 50 | 0 | 92% | 9.3 | 12748 | 22.18 | 574.71 |

## 维度得分（各维度平均）

| model | data-pipeline | debug | fix-and-test | judge | locate | memory-loop | rubric.correction_quality | rubric.evidence_grounding | rubric.identity_evidence | rubric.inspectable_reasoning | rubric.metadata_defiance | rubric.pipeline_correctness | rubric.policy_fidelity | rubric.reproducibility | rubric.reuse_value | rubric.sample_completeness | rubric.scope_control | rubric.self_consistency | rubric.source_priority | rubric.statistic_literacy | rubric.truncation_awareness | script-dev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | 0.98 | 0.96 | 0.98 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |

## 任务明细

| model | task | passed | steps | tokens | elapsed_s | accounted_tok/s | judge | failure |
|---|---|---|---:|---:|---:|---:|---|---|
| deepseek-v4-flash | clean-aggregate-001 | Y | 6 | 5534 | 10.55 | 524.55 | - |  |
| deepseek-v4-flash | clean-aggregate-002 | Y | 5 | 3811 | 6.69 | 569.66 | - |  |
| deepseek-v4-flash | data-pipeline-003 | Y | 5 | 6213 | 10.80 | 575.28 | - |  |
| deepseek-v4-flash | data-pipeline-004 | Y | 5 | 4389 | 8.44 | 520.02 | - |  |
| deepseek-v4-flash | data-pipeline-005 | Y | 5 | 3008 | 5.44 | 552.94 | - |  |
| deepseek-v4-flash | data-pipeline-006 | N | 7 | 10047 | 15.51 | 647.78 | - | state_assert:state.outputs.net_revenue.rows == [['2025-01', 'north', '576.00', '2'], ['2025-01', 'south', '390.00', '1'], ['2025-02', 'east', '300.00', '1'], ['2025-02', 'south', '213.00', '1'], ['2025-02', 'unknown', '71.00', '1']] |
| deepseek-v4-flash | data-pipeline-007 | Y | 8 | 13070 | 17.29 | 755.93 | 1.00 |  |
| deepseek-v4-flash | data-pipeline-008 | Y | 9 | 25528 | 17.86 | 1429.34 | 1.00 |  |
| deepseek-v4-flash | data-pipeline-009 | Y | 6 | 12082 | 18.67 | 647.13 | 1.00 |  |
| deepseek-v4-flash | debug-stacktrace-001 | N | 5 | 6686 | 12.22 | 547.14 | - | state_assert:state.hidden_tests.applicable and state.hidden_tests.total >= 24 and state.hidden_tests.passed == state.hidden_tests.total |
| deepseek-v4-flash | debug-stacktrace-002 | Y | 7 | 4511 | 8.60 | 524.53 | - |  |
| deepseek-v4-flash | debug-stacktrace-003 | Y | 7 | 6153 | 11.44 | 537.85 | - |  |
| deepseek-v4-flash | debug-stacktrace-004 | Y | 9 | 7361 | 15.19 | 484.60 | - |  |
| deepseek-v4-flash | debug-stacktrace-005 | Y | 14 | 7880 | 17.70 | 445.20 | - |  |
| deepseek-v4-flash | debug-stacktrace-006 | Y | 5 | 7456 | 12.19 | 611.65 | - |  |
| deepseek-v4-flash | debug-stacktrace-007 | Y | 8 | 13475 | 21.13 | 637.72 | - |  |
| deepseek-v4-flash | debug-stacktrace-008 | N | 7 | 27802 | 47.42 | 586.29 | - | state_assert:state.hidden_tests.passed == state.hidden_tests.total and state.hidden_tests.total >= 5 |
| deepseek-v4-flash | debug-stacktrace-009 | Y | 8 | 12929 | 13.55 | 954.17 | - |  |
| deepseek-v4-flash | debug-stacktrace-010 | Y | 25 | 40601 | 65.91 | 616.01 | - |  |
| deepseek-v4-flash | fix-failing-test-001 | Y | 5 | 5184 | 8.93 | 580.52 | - |  |
| deepseek-v4-flash | fix-failing-test-002 | Y | 6 | 7671 | 13.36 | 574.18 | - |  |
| deepseek-v4-flash | fix-failing-test-003 | Y | 7 | 13371 | 29.72 | 449.90 | - |  |
| deepseek-v4-flash | fix-failing-test-004 | Y | 11 | 19992 | 41.85 | 477.71 | - |  |
| deepseek-v4-flash | fix-failing-test-005 | Y | 7 | 4404 | 8.38 | 525.54 | - |  |
| deepseek-v4-flash | fix-failing-test-006 | Y | 5 | 5222 | 8.47 | 616.53 | - |  |
| deepseek-v4-flash | fix-failing-test-007 | Y | 6 | 7475 | 12.19 | 613.21 | - |  |
| deepseek-v4-flash | fix-failing-test-008 | N | 8 | 18229 | 32.30 | 564.37 | - | state_assert:state.hidden_tests.passed == state.hidden_tests.total and state.hidden_tests.total >= 5 |
| deepseek-v4-flash | fix-failing-test-009 | Y | 23 | 57670 | 72.03 | 800.64 | - |  |
| deepseek-v4-flash | liberty-extract-001 | Y | 32 | 47408 | 105.40 | 449.79 | - |  |
| deepseek-v4-flash | locate-bug-001 | Y | 4 | 3403 | 4.66 | 730.26 | - |  |
| deepseek-v4-flash | locate-bug-002 | Y | 5 | 3509 | 5.01 | 700.40 | - |  |
| deepseek-v4-flash | locate-bug-003 | Y | 6 | 5917 | 9.48 | 624.16 | - |  |
| deepseek-v4-flash | locate-bug-004 | Y | 5 | 5117 | 8.46 | 604.85 | - |  |
| deepseek-v4-flash | locate-bug-005 | Y | 4 | 3374 | 5.07 | 665.48 | - |  |
| deepseek-v4-flash | locate-bug-006 | Y | 5 | 4485 | 5.92 | 757.60 | - |  |
| deepseek-v4-flash | locate-bug-007 | Y | 3 | 4677 | 7.49 | 624.43 | - |  |
| deepseek-v4-flash | locate-bug-008 | Y | 6 | 16777 | 15.93 | 1053.17 | - |  |
| deepseek-v4-flash | memory-loop-001 | Y | 8 | 14377 | 28.24 | 509.10 | 1.00 |  |
| deepseek-v4-flash | memory-loop-002 | Y | 10 | 8473 | 17.68 | 479.24 | 1.00 |  |
| deepseek-v4-flash | memory-loop-003 | Y | 10 | 16096 | 31.46 | 511.63 | 1.00 |  |
| deepseek-v4-flash | memory-loop-004 | Y | 10 | 11662 | 22.98 | 507.48 | 1.00 |  |
| deepseek-v4-flash | memory-loop-005 | Y | 9 | 10987 | 18.13 | 606.01 | - |  |
| deepseek-v4-flash | script-dev-002 | Y | 11 | 24057 | 51.33 | 468.67 | - |  |
| deepseek-v4-flash | script-dev-003 | Y | 12 | 11420 | 25.46 | 448.55 | - |  |
| deepseek-v4-flash | script-dev-004 | Y | 11 | 6997 | 16.31 | 429.00 | - |  |
| deepseek-v4-flash | script-dev-005 | Y | 7 | 15941 | 28.10 | 567.30 | - |  |
| deepseek-v4-flash | script-dev-006 | Y | 18 | 13446 | 30.43 | 441.87 | - |  |
| deepseek-v4-flash | script-dev-007 | Y | 28 | 16827 | 39.08 | 430.58 | - |  |
| deepseek-v4-flash | script-dev-008 | Y | 6 | 6340 | 10.23 | 619.75 | - |  |
| deepseek-v4-flash | write-cli-001 | Y | 28 | 28354 | 58.40 | 485.51 | - |  |
