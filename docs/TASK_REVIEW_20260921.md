# 本轮实跑逐题审查（2026-09-21，rebuild-qwen38-27b-0921）

范围：只审这一轮新跑的题——`runs/rebuild-qwen38-27b-0921/Qwen3.8-27B-UD-Q4_K_M-rebuild/`
里已有结果的 23 题，逐条读解答过程，回答两件事：是不是太简单，判据有没有把非能力的东西算成能力失败。
历史群体的难度校准不在此文（另见 `docs/TASK_HARDENING_PLAN.md`）。

被审模型：`qwen3.8-27b-7900xtx`（1235 网关，temp=0 / seed=42 / max_output 16384）。
判分 = 确定性层 + 7 道题的云端裁判（`luna-max:gpt-5.6-luna`，temp=0，`judge_id` 钉死并进指纹）。
23 题：**18 PASS / 5 FAIL / 0 未评**，全程 `retries=0`。

## 1. 五个 FAIL 逐条判定：四个是模型的，第五个已裁定为模型问题

data-pipeline-009 — 判据失败点 `flow_stats.rows` 行序。数值全对（head20 r=0.71 显著、full r=-0.12
不显著、51 窗 11 正），还用 numpy 走了第二条路径复核；但它把行写成 head20、full，题面明写
"按 scope 升序"（`full` < `head20`）。它自己的脚本注释里留着 `# scope ascending: full < head20?`，
然后选了反的那条。判定：**模型问题**。题面、判据、同题另一张表"行序不限"三者自洽；裁判独立给
partial，理由同一处。

debug-stacktrace-001 — 隐藏用例 34/35。可见测试 2→9 全绿，`calc/validation.py` 与 `calc/coupon.py`
都改对了；漏掉 `coupon` 键存在但值为 `None` 的情况，`order.get("coupon", {}).get(...)` 直接
AttributeError。题面写了"没有券时原样返回 total"。判定：**模型问题**，而且这正是隐藏层存在的理由——
可见套件每种边界只给一个样例，照抄就绿。

debug-stacktrace-008 — 隐藏用例 4/5。11 步、55,685 token、727 秒（预算 64k/900s，两项都逼近上限）；
可见 3→9 全绿，`review/scheduler.py` 与 `review/transport.py` 都动了。失败用例
`test_other_conversation_review_survives_foreground_preemption`：跨 conversation 的 review 被前台
抢占后应在空闲后恢复，它返回 `None`。题面第二条明写"其他 conversation 的 review 只能保留一份并在
空闲后恢复"。判定：**模型问题**，但预算要盯着——历史 27 次里 12 次死于 max_tokens/timeout，
本轮是"做完但做错"，说明正解形状离预算不远。

liberty-extract-001 — `termination=max_tokens`。12 步全花在 `source_search`/`source_read_range`
手工读窗口（16 次检索类调用），一次 `run_python` 都没发，69,666 token 撞顶。判定：
**题目自洽，是上限题**。题面第一步就要求有界探查，判据要求 `write_file → run_python` 的顺序，
参考解约 12 次调用，预算够用；它测的就是"会不会从手工读切到写脚本"。41 次历史里只有 6 次写过脚本、
2 次真的跑过 python。

debug-stacktrace-010 — 只败在 `order_of [edit_file, run_tests]`。36 步、51,629 token：它照系统提示词
"先用 run_tests 建立基线"跑了基线（这题的可见 9 条基线本来就是绿的，故障不在可见测试覆盖内），
读了 10 个文件，做了约 20 次 `edit_file`，**之后再没跑过测试**。其余全过：可见 9/9、隐藏 8/8、
改了 ≥2 个文件且含 `breadth/fastscan.py`、没碰 `tests/`、无重复失败调用。判定：**模型问题**——
产品是对的，只因为"改完没复跑"被判负。系统提示词确实写了"改完源码必须再用 run_tests 复跑验证"，
所以判据不是凭空索要；但它不是 §10.9 里被降级的"过程宽度"（读几个文件、搜几次），而是
"改完不验证"这条工程纪律本身。**已裁定（2026-09-21）：判据不动，这条 FAIL 成立**——
"改完不跑测试是模型有问题"；`test_report` 那条也不改（它确实会被基线满足，但删掉顺序判据
等于这题完全不检查验证发生过）。详见 `docs/TASK_INVARIANTS.md` 末节。

## 2. 十八个 PASS：哪些真做了活，哪些题太浅

做了真活、判据咬到了地方（6）：`data-pipeline-007`（6 步/25,896 tok/270s，用 catalog 定价对账
推翻 README 口径，逐单比对净额扣 fee，裁判 pass 理由与脚本数字对得上）、`data-pipeline-008`
（8 步/25,375 tok，两次 run_python 复跑，处理了截断）、`debug-stacktrace-006`
（7 步/21,926 tok/247s）、`debug-stacktrace-007`（6 步、3 次 edit）、`debug-stacktrace-009`
（7 步、**11 次 edit** 的跨模块一致性）、`debug-stacktrace-004`（8 步，先读 4 个文件再动刀）。
共同点：可见测试从红翻绿（3→9、1→9），且必须同时改两个以上模块才翻得绿。

通过但过程偏薄，是这套题里最该加难的一档（7）：`clean-aggregate-002`（4 步/5,033 tok）、
`data-pipeline-004`（5 步/**3,473 tok**/24.6s）、`data-pipeline-003`（5 步/5,826 tok）、
`data-pipeline-005`（7 步/5,440 tok）、`clean-aggregate-001`（6 步/7,877 tok）、
`debug-stacktrace-002`（6 步/**3,941 tok**/25.4s）、`debug-stacktrace-005`（6 步/5,321 tok）。
四五千 token、二三十秒交卷。它们不是判据松（产物数值是精确钉死的），是**环境浅**：脏数据与故障形态
只有题面演示的那几种，一次脚本就写完。加难方向是往环境里加形态（跨月负数、空月份、时区后缀、
第二种失败模式），不是加判据条数。

中间档（5）：`data-pipeline-006`（6 步/12,095 tok，读满 3 个文件才动手）、`debug-stacktrace-003`
（6 步、3 次 edit），以及本轮尚未跑到的 locate / memory / script 家族。

## 3. 审查过程中发现的一个真缺陷：隐藏用例判负时不留逐条证据

`result.json` 里隐藏判据的 evidence 只有一行布尔表达式
（`state.hidden_tests.passed == state.hidden_tests.total => False`），看不出是哪条用例、断在哪句。
我为了判上面两个 FAIL 是不是假负，只能用 `tools/replay_gate.py` 把轨迹灌回环境重放，再从
`state["hidden_tests"]["failures"]` 里取 pytest 尾巴，才看到
`AttributeError: 'NoneType' object has no attribute 'get'` 与 `assert None is not None`。
而 `--runs` 收的是 runs 根目录而不是 run id，我是靠临时软链目录才把重放范围缩到一条轨迹。

该修，且不影响任何判定：把 `hidden_tests.failures` 的用例名（不带长 traceback）写进该条 check 的
evidence；让 `replay_gate.py --runs` 也接受 run id。否则每次隐藏判据判负都要人工重放一遍，
才能回答"是模型没修对，还是用例过严"。

## 4. 顺带纠正一条我先前报错的结论

`memory-loop-004` 历史 0/27 不是难题，是**已修但从未复测**的判据缺陷：旧断言要求记忆句末尾的中文
句号 `。`，25/33 次尝试写的都是不带句号的同一句话。commit `d7bf11e`（09-20 19:45）已把判据改成
`rstrip('。.')` 后比对，而最新的 004 实跑是 09-13 与 09-16——全在修复之前。按当前题面重放那 33 条
轨迹，确定性通过从 0 变成 5/33（15%）。本轮它排在待跑队列里，这是修复后的第一次真实测量。

同类里 `fix-failing-test-008`（历史 3/31）经核查是**真难**：20 次失败只栽在同一条隐藏用例
（`identity_tokens` 长度守卫），题面确实写了那条要求；失败中位 8 步 / 32.5k tok / 291s，离预算很远，
不是跑不完。最小改动是把"position ≠ len(identity_tokens) 时 offer 必须拒绝并抛 ValueError"写进题面
（判据不动），并补一份参考解 + 一条"9/9 绿但漏长度守卫"的负控。

## 5. 待办

1. 模型重启后续跑缺的 27 题：同一 `--out`，`--tasks` 只给缺的 id（`cli.py` 的"续跑"会把选中范围内
   已有结果归档重跑，所以必须缩小 `--tasks`，不能整量续）。
2. 修 §3 的两条可读性缺陷。
3. 对 §2 里"过程偏薄"的 7 题跑 `replay_gate` 量隐藏用例存活率，把存活率 100% 的几道往环境里加形态。
4. ~~`debug-stacktrace-010` 的"产品对、过程违纪"要不要继续判负~~ —— **已裁定：判据不动，
   "改完不跑测试是模型有问题"**（`docs/TASK_INVARIANTS.md` 末节）。

## 6. 二三十秒那几题：一次命中还是题太薄？——两者都是，机制如下

把"是否第一次跑脚本就产出被接受的结果"量出来（debug 家族的 run_tests 第一次是基线，本就该红，
所以只看改完之后的复跑）：

| 题 | 步 | 跑脚本次数 | 首次即产出正解 | 读文件 | 墙钟 |
|---|---|---|---|---|---|
| clean-aggregate-001 | 6 | 1 | 是 | 2 | 58.7s |
| clean-aggregate-002 | 4 | 1 | 是 | 1 | 39.8s |
| data-pipeline-003 | 5 | 1 | 是 | 2 | 46.5s |
| data-pipeline-004 | 5 | 1 | 是 | 1 | **24.6s** |
| data-pipeline-005 | 7 | 2 | 否（回改一次） | 1 | 39.2s |
| debug-stacktrace-002 | 6 | 基线+复跑1次 | 改完一次即 8/8 | 5 | **25.4s** |
| debug-stacktrace-005 | 6 | 基线+复跑1次 | 改完一次即 8/8 | 5 | 37.5s |

`data-pipeline-004` 的轨迹最能说明问题：`monthly.csv` **只有 6 行**，三个陷阱（2025-02 重复取
`updated_at` 最新、2025-03 缺月补 0、2025-04 值为 0 时环比写 NA 而不是 -100%）在一次 `read_file`
里就全部落在同一屏内。模型写的脚本是对的，但它不需要脚本也看得见所有边界。于是这道题的考点
（题面自己写的"必须用脚本算，不得照抄"）退化成了"三条肉眼可见的规则"。
`debug-stacktrace-002` 同理：仓库是 `table/grid.py` + `table/col.py` 两个各 4 行的函数，
失败语义是负索引，模型加两个守卫、复跑 8/8、隐藏用例全过——25 秒。

所以结论不是"题目坏了"：这些题的产物判据是精确钉死的，历史群体也确实有 15–20% 的模型失手，
它们不是零方差题。结论是**它们测的是"会不会写十行 csv/边界处理"，测不到"规模下还能不能保持正确"**。
加难的唯一有效杠杆是把环境放大到陷阱无法同屏枚举（几百行、重复月与缺月同时出现、0 与负值混排、
需要 join 的第二张表），而不是加判据条数——这正好对上 `data-pipeline-007/008/009` 与
`debug-stacktrace-009/010` 这几题本轮表现出的形状（12k–26k token、多次读文件、6–8 步才交卷）。

顺带记一条我自己差点写错的判读：读 `debug-stacktrace-002` 时我一度以为并行 `read_file` 的观测串了
（三次调用都返回 `table/grid.py`），实际是我按名字取观测、没按序号对齐造成的取数错误——环境的
并行调用与观测是一一对应的，不是缺陷。

## 7. part 2 续跑后的新错题（同一套读法：先取逐条证据，再判题还是模型）

整行进度 43/50：36 PASS / 7 FAIL / 0 未评。新增两道错题的逐条证据：

**fix-failing-test-004 — 隐藏 30/32，判：模型问题，但题面该补一句。**
可见 8/8 全绿，失败的是 `test_empty_fields_are_ignored_at_either_end_and_in_the_middle`
与 `test_a_record_of_only_empty_fields_reports_zero_counts`，实断都是
`parse_numbers('')` 抛 `ValueError: expected exactly one CSV record`——它把"必须恰好一条记录"
的守卫放在了忽略空字段**之前**，于是空文本被当成非法输入。
为什么算模型问题：可见套件里 `test_empty_input()` 已经把"空 → 全 0"这个语义钉死了一次
（`summarize(" , ") == {count:0, total:"0.00", mean:"0.00", median:"0.00"}`），题面也写了
"空字段忽略"，把这条规则推广到 `""` 与 `", , ,"` 正是隐藏层的本职。
为什么要补题面：`""` 严格说是"零条记录"，落在"输入是一条记录、多记录拒绝"的两种读法之间，
属于可辩护的歧义。按"严格但已隐含 → 写进题面、判据不动"的既有裁定，应加一句
"空文本/零条记录同样按'无数字'处理（count=0、total/mean/median=0.00），不按非法输入拒绝"。
这不降低难度，只是把尺子上的刻度画清楚。另注：这题的隐藏模块今早 08:24 才扩过用例
（commit 1693558 之后仍有未提交修改），历史 26/27 是**旧版判据**的读数，本轮是新版的第 1 次实测。

**memory-loop-004 — 判：模型问题（本轮是判据修好后的第 1 次真实测量）。**
调用序列 `list_files → memory_search → read_file → write_file → run_bash → done`：
它检索了记忆、按已批准策略逐字生成了 `out/rollout.csv` 与 `release_note.md` 并跑脚本核对，
然后**直接 done，一次 memory_write / memory_update 都没发**。四条失败判据全部指向记忆闭环
（缺 agent-origin 条目、write_calls==1、tool_used memory_write、order 里的 memory_write 环节）。
系统提示词是显式要求的："任务完成前必须闭合记忆，且一次任务只允许一次记忆变更"，
与本轮通过的 memory-loop-003 用的是同一段契约文本，所以不是"题面没写"。
句号陷阱（`。`）已在 commit `d7bf11e` 修掉，本轮 CSV 判据确实通过了——历史 0/27 与本轮的
失败原因已经不是同一件事。

**locate 家族本轮 6 连过（22–37 秒、4–6 步）**，包括历史只有 33% 的 `locate-bug-001`。
读 `locate-bug-001` 的轨迹：6 次 read_file + 2 次 grep + 基线 run_tests，四个键全中——
它的 33% 来自模型真答错（cause 三选一、file/symbol 要自己定），不是过程判据卡人，
所以这题合格。而 `locate-bug-002/003/005` 历史 26–27/27，薄在仓库只有 3–4 个文件、
选项式答案把搜索空间压到个位数，与 §6 的"环境浅"是同一个机制。

## 8. 整行收尾（50/50）与一条重要修正

最终：`Qwen3.8-27B-UD-Q4_K_M-rebuild` **43/50 = 86%**，`未评 0`，`retries 0`，
平均 8.5 步 / 14,596 token / 136.6 秒，accounted 106.82 tok/s。
维度分：fix-and-test 0.98、debug 0.95、data-pipeline 0.94、locate 1.00、memory-loop 0.90、
裁判层 0.90。唯一被裁判打低的仍是 `memory-loop-004`（identity_evidence 0、reuse_value 0），
它的理由与确定性层同因：没调用 `memory_write`。两层再次一致，没有互相打架的判例。

part 2 尾部八题全过，但**步数分布推翻了我 §6 里一个隐含说法**：
`write-cli-001` 历史 26/26 零方差，本轮这个模型却用了 **22 步 / 21,325 token / 185 秒**；
`script-dev-004/005/007` 各 19 步、`script-dev-002` 14 步、`fix-failing-test-009` 14 步。
所以"零方差"不等于"简单"，准确的说法是：**这些题有工作量、但没有失手风险**——
它们能把模型拖到 20 步，却没有一条判据能因标准做法的偏差而判负。
加难的目标因此不是"多干活"，而是"让某种看起来正确的做法真的会错"：
往环境里放会让常规实现失效的形态（§3、§6 的机制不变，只是措辞更准）。

另外提醒读数口径：86% 不能与旧基线（qwen3.8 55%、ternary 75%）直接比——那两行是
40 题旧套件、旧判据（无隐藏泛化用例、基础设施死亡记成能力失败、17 题带裁判闸门）。
本轮是 50 题、27 题有隐藏闸、7 题带钉死身份的裁判、未评不进分母。要跨行比较，
只能在新指纹上重新积累第二、第三个模型。

## 9. fix-failing-test-004 题面补句后的复跑（含一次我自己造成的返工）

补句（判据一字未动，指纹 `658dfa22` → `2bbca732`）后单题复跑：**PASS**，
12 步 / 26,291 token / 322.4 秒，隐藏 32/32 全过。补句前是 FAIL（10 步 / 16,096 token / 181 秒，
隐藏 30/32）。改对的形状很具体：守卫从 `if len(records) != 1: raise` 变成
`if len(records) > 1: raise`，并给 `parse_numbers` 写了与新口径一致的 docstring；
它这次还多跑了两次 `run_tests`（3 次 vs 1 次）。整行因此从 43/50 = 86% 变成 **44/50 = 88%**。

如实记一条代价：这条补句**确实把该模型从判负变成了判过**。所以"只是把刻度画清、难度不变"
这句话在这个模型上不成立——它把这个具体 hinge 从"要猜对 examiner 的口径"变成了"照题面实现"。
判据没动、隐藏用例没删、其它 49 题不受影响，但如果以后有人拿这题的通过率说事，
必须记得：26/27 的历史读数是**旧题面 + 旧隐藏模块**的，与新指纹不可混算，需在新版本上重积累。

返工记录（工具层，不是判分问题）：这次复跑我用了 `--out <已有 run>` 但没加 `--merge-run`，
而 `existing_manifest` 只在 `--merge-run` 分支里赋值——于是 else 分支用"本轮任务集"重写了
manifest，`tasks` 与 `task_fingerprints` 从 50 缩成 1，`started_at` 也被覆盖。
part 2 那次 27 题续跑同样没加 `--merge-run`，所以它写进 `_HISTORY.jsonl` 的那条只记了 27 题。
已按 run 目录内实际存在的 50 份结果重建 manifest（题集、全部指纹、suite_fingerprint、
started_at 恢复 19:30:39，并在 manifest 里留 `repair_note` 说明），并用 `_append_history`
的按-run-幂等语义把历史条目改写成 44/50。同时在 `cli.py` 加了门槛：
`--out` 指向已存在 run 且未加 `--merge-run` 时直接拒绝（rc=2），提示改用 `--merge-run`
（那条路径才会保留原题集并把被复跑的旧尝试归档进 `.attempts/`）。
补句前的原始 attempt 已备份在 `.scratch/fix004_prefix_attempt_backup/`。
`cli.py` 不在 `_FRAMEWORK` 指纹清单内，所以这次修门槛**不**换任何题目的指纹。
