# 验收证据（可重跑的捷径反例）

`docs/TASK_HARDENING_PLAN.md` §6 要求每道加难的题都过两关：参考解必须全项通过，
"可见测试绿但根因未修"的捷径解必须失败。这里存的是**反例那一半的可执行版本**——
加难度数这件事如果没有能重跑的负控，就只是文档里的一句话。

- `ref/` — 各题的参考实现（按题面写出、用于把 `shell_hidden.EXPECTED` 从猜测变成实测）。
- `negative_controls/` — 每份都是一个具体的错法，文件名即错法。

## 怎么重跑

**code_repo 题（fix-\*/debug-\*）的反例现在是自包含可执行脚本**，直接跑就行，退出码 0 表示
"可见全绿 + 隐藏红"这个预期成立：

```bash
python3 docs/hardening_evidence/negative_controls/NC-001-facade-bypass.py
python3 docs/hardening_evidence/negative_controls/NC-debug-stacktrace-007-hardcoded-slots.py
python3 docs/hardening_evidence/negative_controls/NC-fix-failing-test-002-mutant-matrix.py   # 六份变异一张表
```

shell_ws / data_ws 题的反例不是独立可执行断言，需要挂进快照再走真实的隐藏检查通道：

```python
import shutil, sys; sys.path.insert(0, ".")
from evalkit.envs.shell_hidden import run_hidden_checks   # code_repo 用 code_hidden

SNAP, SCRIPT = "script-002", "freq.py"
src = f"docs/hardening_evidence/negative_controls/NC-F2-lower-fold.py"
ws = "/tmp/probe"                                          # 干净工作区
shutil.rmtree(ws, ignore_errors=True)
shutil.copytree(f"environments/shell_ws/snapshots/{SNAP}", ws)
shutil.copy2(src, f"{ws}/{SCRIPT}")
print(run_hidden_checks(SNAP, ws))                         # 期望 passed < total
```

`tools/replay_gate.py <task_id>` 是配套的定标工具：把历史轨迹灌进当前题面，
输出"曾经判对的尝试现在还剩多少"。用它之前先读 §10.2——存活率只在
"加难没有改变题面向模型索要的东西"时才是合法样本。

## 当前状态

| 反例 | 针对 | 可见测试 | 隐藏结果 | 被哪个用例抓住 |
| --- | --- | --- | --- | --- |
| `NC-F1-lower-wplus` | script-dev-002 | 红（`\w+` 留下 `_`） | 3/6 | 分隔符集 |
| `NC-F2-lower-fold` | script-dev-002 | **绿** | 4/6 | `casefold-expansion-and-ties` |
| `NC-F3-isalpha-length` | script-dev-002 | **绿** | 2/6 | `separators-and-digit-length` |
| `NC-F4-stopwords-retokenized` | script-dev-002 | **绿** | 5/6 | `stopword-lines-are-whole-lines` |
| `NC-A1-filter-before-dedup` | script-dev-006 | 红（示例的 500→200 已禁止） | 4/8 | 去重次序 |
| `NC-A2-last-row-dedup` | script-dev-006 | **绿** | 6/8 | `dedup-order-and-superseded-latest` |
| `NC-A3-untyped-fields` | script-dev-006 | **绿** | 7/8 | `non-string-field-values` |
| `NC-001-facade-bypass` | fix-failing-test-001 | **绿**（9/9，四条原有判据全过） | 28/32 | `inclusive_stop` 两点 + `weighted_mean` 分母/零权 |
| `NC-D2-first-row-anchor` | debug-stacktrace-002 | **绿**（8/8，四条原有判据全过） | 24/36 | 以 `grid[0]` 为宽度/列和锚点、就地补零污染调用方 |
| `NC-D5-live-spec-recovering-quota` | debug-stacktrace-005 | **绿**（8/8，四条原有判据全过） | 12/18 | 默认参数捕获活 dict、配额触发后又重开 |
| `NC-F3-normalize-layer-facade` | fix-failing-test-003 | **绿**（8/8，四条原有判据全过） | 310/317 | 规范化层外包：只在 slug 入口挡非字符串 |
| `NC-F3-separator-whitelist` | fix-failing-test-003 | **绿**（8/8，四条原有判据全过） | 203/317 | 分隔符只认可见套件演示过的空格/下划线/连字符三种 |
| `NC-F5-half-isolation` | fix-failing-test-005 | **绿**（9/9，三条原有 state_assert 全过） | 113/128 | 只 deepcopy `base` 侧，`override` 侧原样交引用 |
| `NC-debug-stacktrace-007-cross-slot-wipe` | debug-stacktrace-007 | **绿**（9/9，其余判据全过） | 16/17 | `test_new_prompt_on_one_slot_leaves_another_slots_in_flight_prompt_intact` |
| `NC-debug-stacktrace-007-hardcoded-slots` | debug-stacktrace-007 | **绿**（9/9，三处根因都真修了） | 13/17 | 第 1、2、7 组各一次：`Spy(3)`/`Worker(4)` 打到 slot 2 即 KeyError |
| `NC-fix-failing-test-002-fake-half-up` | fix-failing-test-002 | **绿**（8/8，其余判据全过） | 6/9 | 负数半分 `negative_half_way_...` + 另两条 |
| `NC-fix-failing-test-002-mutant-matrix` | fix-failing-test-002 | 六份**全绿**（8/8） | 6/9–8/9 | 一张表：rules 注释糊弄 / 绕二进制浮点 / `+0.5` 冒充 HALF_UP / 千分位只剥一组 / 默认币种漂移 / 括号各自剥 |

## 隐藏用例的"咬合力"要分开报

放松过程计数之后（`2e43e9e`），隐藏用例是唯一的防假对手段，所以每道题都要报两个数：
反例是否被抓住，以及**历史通过群体还剩多少**（`tools/replay_gate.py <题> --only-passed`）。

| 题 | 隐藏条目 | 原始快照 | 正确解 | 历史存活 | 判读 |
| --- | --- | --- | --- | --- | --- |
| fix-failing-test-001 | 32 | 5/32 | 32/32 | 29/29 (100%) | 通过群体零方差，只挡捷径不提难度 |
| debug-stacktrace-002 | 36 | 5/36 | 36/36 | 32/32 (100%) | 同上 |
| debug-stacktrace-005 | 18 | 2/18 | 18/18 | 25/25 (100%) | 同上 |
| fix-failing-test-005 | 128 | 68/128 | 128/128 | 27/28 (96%) | 咬到 1 条 |
| fix-failing-test-003 | 317（原 61） | — | 317/317 | **16/28 (57%)** | 与 61 条时**同一个** 57%，多出的 256 条零额外杀伤 |
| debug-stacktrace-007 | 17 | 0/17 | 17/17 | 30/30 (100%) | 通过群体零方差，只挡捷径不提难度 |
| fix-failing-test-002 | 9 | 2/9 | 9/9 | 24/24 (100%) | 同上 |

003 那 12 条死亡按用例分：7 条死在 `max_length` 的 11 字符边界（`alpha-beta-gamma` 必须收成
`alpha-beta`，题面"不能留下半个尾部单词或连字符"直接覆盖），2 条死在"规范化层自身要拒绝
非字符串"——这一条原题面只是隐含，所以已把它写进 `task_prompt`，从此它是**遵循已声明契约**
而不是考揣摩出题人。

003 从 61 条扩到 317 条之后存活率仍是 16/28：**同一个 57%，一个人都没多杀**。这 256 条买到的
不是难度而是**覆盖面**——分隔符 44×2 全集扫描、12 个组合音标、6 段文本 × 16 个切割长度、
16 种被拒对象类型，为的是挡掉"只认可见套件演示过的三种分隔符"这类只背样例的修法
（`NC-F3-separator-whitelist` 在它身上红 114 条）。模块里同时写明了一批**刻意不钉**的分歧点
（正好切在词边界、`max_length` 装不下一个整词、非 Latin-1 字母、撇号以外的引号形状、
NFKD 与 casefold 的先后），因为历史群体在这些问题上真的分裂，钉任何一边都是移门柱。
这条判断要留档：**条目数不是难度指标，存活率才是**；以后有人拿"隐藏用例从 61 涨到 317"
当加难证据，先看 `replay_gate` 的存活率有没有动。


`NC-001` 是"症状修"而非"根因修"的代表形态：`inclusive_values` 自己 +1 而不去问
`inclusive_stop`，`weighted_avg` 自己算分母而不交给 `weighted_mean`——两处缺陷都还活着，
每处只加一行注释就算"改过这个文件"，所以 `files_changed` 那条判据区分不了修好与没修好。
它挂掉的 4 条隐藏项正是可见套件结构上看不见的东西：可见测试从不直接调用那两个定义点。

"可见测试绿 + 隐藏失败"这两列同时成立，才说明隐藏闸门在补可见测试的漏洞而不是重复它。

现在覆盖 script-dev-002/006、fix-failing-test-001/002/003/005、debug-stacktrace-002/005/007。
仍缺反例文件的是 locate-bug 与 data-pipeline 两族：它们的隐藏通道是状态断言而不是 pytest，
反例要按同一格式另写一份"终态凑对但根因未修"的工作区快照才能进这张表。

一个结构性副作用要记住：隐藏测试是按**快照名**解析的（`hidden_tests/<snapshot>.py`），不是按题号。
`fix-failing-test-001` 与 `locate-bug-001` 共用 `calc_buggy` 快照，所以新增这份隐藏测试同时把
两题的指纹换掉了（locate-001 不读 `state.hidden_tests`，判定不受影响，已复核 SOLVABLE+STABLE，
代价是它收尾多跑 0.4 秒）。以后给共用快照加隐藏用例，先 `grep -l "snapshot: calc_buggy" tasks/*/*.yaml`
看会牵动哪几题。
