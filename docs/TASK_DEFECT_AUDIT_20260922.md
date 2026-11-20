# 题目缺陷审计（2026-09-22）：三行对照定位到题目层的错误

## 0. 方法：三行各管一件事，判定只看题目内在证据

| 行 | 端点 | 结果 | 平均（过题）步 / token / 墙钟 | 本次用途 |
|---|---|---|---|---|
| Qwen3.8-27B-UD-Q4_K_M（重建基线） | 本地 1235 | 44/50 = 88% | 7.9 / 12,123 / 105.4s | 参照系 |
| DeepSeek-V4-Pro-Qwen3.5-9B-MTP-Q8_0 | 本地 | 20/50 = 40% | 13.2 / 10,398 / 134.7s | **低能力探针**：连它都一次过的题，说明环境没给犯错面 |
| deepseek-v4-flash | 外部 API | 46/50 = 92% | 9.6 / 12,492 / 21.8s | **缺陷探针**：27B 过而它挂 → 缺口与模型强弱无关，优先怀疑题面 |

三行 0 未评、0 重试，判分全部有效；flash 行的密钥只经 `--api-key-env` 传入，manifest 与全部产物里 0 次出现（`grep` 全库已验）。

**纪律**：通过率只用来指向"哪里值得细看"，不用来做判定。每条结论的证据只有三种——题面原句（带行号）、判据字面量 / 隐藏用例原文、以及我在 `/tmp` 里只按题面写的参考实现。**没有任何一条改动的理由是"多少模型过或没过"**。

## 1. 三行一致性分组（50 题）

| 组 | 题数 | 题号 | 归属 |
|---|---|---|---|
| 三行全过 | 18 | clean-aggregate-001, data-007, debug-002~007, debug-009, fix-001, fix-004, fix-007, locate-002~005, locate-008, script-008 | §6 无区分度候选（含 §4.7 的"数据没样本"机制） |
| 只有 9B 挂 | 24 | clean-aggregate-002, data-003/004/005/008, fix-002/003/005/006/009, locate-001/006/007, memory-001/002/003/005, script-002~007, write-cli-001 | §7 模型层（其中 5 题同时暴露 §3 的 A 类缺口） |
| 27B 过、9B+flash 挂 | 2 | **data-pipeline-006、fix-failing-test-008** | §2 两条确认缺陷 |
| 只有 27B 挂 | 2 | data-pipeline-009、debug-stacktrace-010 | 模型层（debug-010 已裁定：改完不复跑是模型问题） |
| 三行全挂 | 2 | **debug-stacktrace-001、debug-stacktrace-008** | §5 已证可解，题目无错 |
| flash 过、两本地挂 | 2 | liberty-extract-001、memory-loop-004 | 可解性由外部行证实；本地死于 token 预算形状（liberty 另含 §3 A5） |

"27B 过而 flash 挂"只有两题，两题都命中真缺陷——这条探针有效。

## 2. 确认的题目缺陷：四条 B 类（判据把答案钉向题面没选的那一边），十二条 A 类（判据合理但题面未写）

分类定义（与既有裁定一致）：**A 类**＝规则真实存在但只隐含，正确动作是把它写进题面、判据不动；**B 类**＝题面或环境把两种读法都支撑住了，判据却钉死其中一种，属于"考猜 examiner"。**（§2.1–§2.5 共 4 条 B 类）逐条都有题面行号级依据；凡能靠"环境小 / 数据没样本"解释的，一律记在 §4.7（漏区分度）而不是算判据错。**

### 2.1 B 类：fix-failing-test-008 的隐藏用例考的是异常类型，且环境给了反方向的惯例

`evalkit/envs/hidden_tests/fix-008.py:7`：

```python
def test_identity_length_must_equal_position():
    chain = CheckpointChain()
    with pytest.raises(ValueError):
        chain.offer(3, (1, 2), boundary="message")   # position=3，identity 只有 2 个 token
    assert chain.anchors == []
```

1. 题面只说 **"checkpoint 的 position 必须与完整 identity token 数一致"**，没规定不一致时如何反应；
2. `ValueError` 在本题**题面、可见测试、快照源码**里出现次数均为 0（`grep -rn "raise\|ValueError" environments/code_repo/snapshots/fix-008` 无输出）；
3. 可见测试反而立了**相反**的惯例：`test_non_message_prefill_splits_do_not_create_checkpoints` 断言非法 boundary 时 `offer(...) is None` 且 `anchors == []`——即"非法输入 → 静默拒绝、不落盘"。照这条惯例实现"position 不一致"的题面要求，完全符合题面文字，却恰好挂在这一条上。

所以它测的是异常类型而不是缺陷本身。**建议（判据不动，题面补一句）**：

> position 与完整 identity token 数不一致属于非法输入，必须抛 `ValueError`（不得像非法 boundary 那样静默返回 None，也不得留下 anchor）；

代价：fix-008 `task_fingerprint` 变更。**我未落盘，等你定。**

### 2.2 A 类：data-pipeline-006 的 CSV 小数位文本形式（唯一已落盘的改动）

判据按字符串逐格比较 `out/net_revenue.csv`，要求 `'576.00'`；原题面第 6 条只写 **"net_cny 四舍五入到两位小数"**，`round(x, 2)` 完全满足这句话而写出来是 `576.0`。全套件对照：clean-aggregate-001:16、clean-aggregate-002:16、data-003:16、data-004:15、data-005:16、data-007:28、data-008:26（还补了"不足两位补零"）、data-009:24、liberty-001:27 **全部写"固定 N 位小数"**，只有本题离群。

代价统计（不是判定理由）：全历史 45 次尝试里 **10 次数值逐格全对、只因写成 `576.0` 被判失败**。

已按"补题面、判据一字不动"落盘（第 6 条 + `budget:` 后的证据注释），本题指纹变更、历史 26/39 不再同尺。**这是本次唯一改动，可一键回退。**

### 2.3 B 类第二条：locate-bug-004 的题面对环境做了一句**事实错误**的陈述

题面第 13 行：**"候选排序、坐标轴顺序、经度归一化、二维距离包装和经度环绕策略分别在不同模块中。"**
快照实际只有 4 个模块：`geo/nearest.py`、`geo/dist.py`、`geo/normalize.py`、`geo/longitude.py`，其中**坐标轴顺序与二维距离包装同在 `dist.py`** —— 五个关切、四个文件，"分别在不同模块中"为假。这句话正是模型用来决定"往哪儿读"的地图，而它恰好把模型引离判据（同文件第 32 行）钉死的必读文件 `geo/dist.py`。
修法（判据不动）：按快照改写该句 —— 候选排序在 `geo/nearest.py`；坐标轴顺序与二维距离包装同在 `geo/dist.py`；经度归一化在 `geo/normalize.py`；环绕策略在 `geo/longitude.py`。

### 2.4 B 类第三条：fix-failing-test-003 隐藏用例要求"非字母数字一律变分隔符"，而**随发源码示范的正是相反做法**

- 隐藏用例 `evalkit/envs/hidden_tests/fix-003.py:280` 定义 56 个 `SEPARATOR_CHARS`（含 `.` `/` `+` `~` 与全角族），`:300` 断言 `("a/b","ab")`、`("a.b","ab")`、`("a+b","ab")` 两两不得撞成同一 slug —— 即"每个非字母数字字符都**成为**分隔符并合并成一个连字符"。
- 题面只说"保留字母数字语义、合并分隔符"，可见测试示范的分隔符仅空格 / `_` / `-`。
- 决定性的一条：快照随发实现 `text/slug.py:8` 是 `re.sub(r"[^a-z0-9-]", "", value)` —— **删标点**。模型沿"修 bug"的常规读法保留这行语义（只补 casefold/上限），`a/b` 与 `ab` 就会撞车；反之按隐藏用例的口径改写则要动一条题面没点名要动的规则。隐藏文件自己的注释（`:276`）也承认"把标点删掉"和"只认这三种"都能过可见测试。→ 判据在两种都有可读依据的实现里钉了其中一种。
- 建议（判据不动，题面补两句）：**"分隔符不是某个字符名单：凡折叠后不属于 `[a-z0-9]` 的字符一律当分隔符参与合并（因此 `a/b` 与 `ab` 不得同 slug）；只有撇号族与组合音标按下一条删除。"** 加一句反向排除：**"引号族不单独设判。"**

### 2.5 B 类第四条：debug-stacktrace-005 要求改 `funcs/state.py`，却没有任何可观察行为需要这一改动（我已实跑复现）

判据第 4 条（debug-stacktrace-005.yaml）：`'funcs/state.py' in state.files_changed and 'funcs/make.py' in state.files_changed`。
而隐藏用例文件自己的说明（`evalkit/envs/hidden_tests/debug-005.py:22`）写着 **"Deliberately NOT asserted: the shape of `funcs/state.py`"** —— 设题说明与判据互相矛盾。

实证：在 `/tmp/ref005` 里**只改 `funcs/make.py`**（工厂内 `seen` 集合拒重名、`limit<=0` 拒绝、每个 handler 闭包自带 `box`/`offset`/`limit`，`state.py` 一字未动），**26 passed = 8 可见 + 18 隐藏全绿**；题面那句"限额按 handler 且按 factory 隔离，新工厂必须是新状态"在这种实现下完全成立。也就是说：**一份符合题面、通过全部可观察测试的解会被这条 gate 判负**，它考的是"你改没改那个文件"，不是行为。

代价核算（去掉 gate 会翻转多少历史判定）：本指纹下可见的 3 次全绿尝试**都改了两个文件，0 次翻转**；本轮三行也都没触发。所以这条目前只伤害"未来的合法解"，不改变已有成绩。
两条出路（都在"不放宽能力要求"的前提下）：
1. **让要求可观察**（推荐）：题面补一句 **"`funcs/state.py` 的模块级 COUNTS 必须改造成按 factory 隔离的状态、handler 仍经它记账，不得把它留成无人调用的死代码"**，并加一条隐藏用例直接断言 `state` 层的隔离（那时 files_changed 这条就成了冗余保险，可以留着）；
2. 或者删掉该 check 里 `state.py` 那一半、只留 `make.py`，并按上面的 0/3 翻转数登记在案。


## 3. 其余 A 类缺口（判据全部保留，各补一句题面）

**A2　data_ws 全族：`run_python` 允许 `code=` 内联，判据却要求 `write_file`**
`evalkit/envs/data_ws.py:76` 的工具自述写着"传 `script=<路径>` 跑脚本文件，**或传 `code=<代码串>` 直接执行**"；题面只写"编写并运行 Python 脚本"（如 data-003:9）／"将逻辑写入脚本并运行"（clean-001:18），而判据是 `order_of: [read_file, write_file, run_python]`（data-003:29）。用 `code=` 内联的实现在题面与工具说明下完全合法，却必然挂 `order_of`。本轮 9B 在 data-003 就死在这里（数值逐格正确，`seq` 里没有 write_file）。
建议原句：**"必须先用 write_file 落成 .py 脚本，再用 run_python 的 script 参数执行；不接受 code= 内联。"**

**A3　script-dev-002/004/006：held-out 用例要求 CLI 自建 `--out` 父目录，题面未写**
held-out 在临时目录里以 `--out held/freq.csv` 等路径运行（`evalkit/envs/shell_hidden.py:120/232/253`），而这三题题面只给 `--out out/xxx.csv` 的示例、从未说脚本要负责建目录。结果是 7 个 held-out 用例整批死于 `FileNotFoundError`，与解析逻辑无关。参考实现都含 `os.makedirs`，说明这条要求可实现，只是没说。
建议原句：**"写结果前脚本须自行创建 `--out` 的父目录（`os.makedirs(dirname, exist_ok=True)`），不得假设目录已存在。"**
（对照：script-003/008 提到"目录"是讲工作目录解析，不是这条要求，不能算已写。）

**A4　locate-bug 全族：`answer_json.symbol` 的取值形式未定义**
locate-001~008 的题面统一只写"只能包含 file、symbol、cause、decoy 四个键"（如 locate-006:26），cause/decoy 有枚举可抄，`symbol` 却没有任何格式约定；判据钉的是定义处的裸名字 `'quote_key'`。9B 写 `def quote_key(sku, currency): return sku` —— 语义正确、字面不同，直接判负。
建议原句：**"symbol 只写定义点的函数名／变量名本身（不带 `def`、参数表或任何源码片段）。"**

**A5　liberty-extract-001：题面给的命令行与 `run_python` 的参数形状不一致**
题面第 30 行以 shell 命令形式给示例，模型把整串塞进 `script=` 后得到 `no such script: ...`（`data_ws`/`large_file_ws` 同一条 `script` 语义，见 A2 引用的实现），9B 因此以同一形状重复失败 10+ 次耗尽 36 步。示例本身没错，但它与工具契约之间缺一句话。
建议原句：**"第 30 行是等价命令行；用 run_python 时 `script` 只填脚本路径，其余参数放进 `args` 数组。"**

**A6　memory-loop-001：判据要求记忆正文点名两个字段名，题面却完全没提"写什么"**
判据（memory-loop-001.yaml:26）要求某条 agent 记忆的 `content` 同时包含 `'order_id'` 与 `'updated_at'` 两个字串。题面（同文件 task_prompt 全四行）只说"处理版本化 orders.csv…最新版本为 cancelled 的订单应整体排除"，**从未点名去重主键与版本时间字段**；通用 system_prompt 里的记忆义务是泛写（"将新验证且可复用的结论写入记忆"）；seed 条目 `finance:daily-export-contract` 的正文里也没有这两个词。两个词只在 `orders.csv` 表头出现——所以模型读得到字，却读不到" lesson 必须落到字段名这一层"这条要求。考点（写可复用的具体口径，而不是复述一句废话）是真实的，只是隐含。
建议原句（接在 task_prompt 第一句后）：**"闭环时把去重口径写进记忆：按哪个订单主键、按哪个版本时间字段取最新，字段名照抄输入表头。"**
（同文件 L28 的 `write_calls + update_calls == 1` 比 system_prompt"已有条目时不得另写并存新条目"更宽——这是偏宽而非偏严，两种动作都可能正确，不算缺陷。）

**A7　script-dev-002：隐藏用例把 Unicode 规范化方式钉死，题面只说"按 Unicode 字母或数字的连续串分词"**
`evalkit/envs/shell_hidden.py:117` 的输入同时给了预组合 `café` 与分解式 `cafe + U+0301`，`:704` 的期望是 `["cafe","1"], ["café","1"]` 两个不同 token —— 即**不得做任何 NFC/NFD 规范化**；另有 `casefold-expansion-and-ties`（`:1009`）钉死 casefold 只能作用于切出的 token、不能先折全文（`Straße/STRASSE`、`１２` 全角同理）。题面第 12–13 行没有写这两条，先 `casefold()` 全文或先 `unicodedata.normalize()` 的两种常规写法都会整批挂掉。
建议原句：**"分词按输入里的原始字符判定，casefold 只作用于切出的每个 token；不做任何 Unicode 规范化（NFC/NFD 都不做）。"**

**A8　script-dev-003：`grand` 行的 avg 口径未定义，可见数据恰好分辨不出来**
题面只说 grand 是全局汇总。可见数据下"Σtotal ÷ Σcount"与"各文件 avg 的平均"都是 8.33，肉眼不可辨；隐藏用例（`shell_hidden.py:713` 期望 `["grand","4","13.53","3.38"]`，13.53/4=3.3825→3.38，而各文件 avg 的均值是 3.96）只接受前者。→ 题面把唯一决定权留给了判据。
建议原句：**"grand 行的 avg = 全部有效记录的 total 之和 ÷ 全部有效记录的条数，不是各文件 avg 的平均。"**

**A9　script-dev-006 / 007 / write-cli-001：`order_of` 要求先 `read_file`，题面却已把表头/字段写全**
判据（如 script-dev-006:44–45）钉 `read_file → write_file → run_bash → run_bash`，而题面第 16–19 行已经把五个字段与列名全给出来了——"不读输入直接写脚本"在题面下完全合法，却会被判失败。这与 A2 同族（题面允许的合法路径被过程判据拒掉）。
建议原句：**"动笔前必须先用 read_file 读过输入文件（只照抄题面字段清单不得分）。"**；script-006 另需补 **"ts 完全相同时保留文件中后出现的一条"**（题面第 20 行"保留 ts 最晚"未定义并列，其余三题都写了"后出现"）。

**A10　data-pipeline-004：`value` 列写"输出普通数字"，判据却要输入里的整数形状**
题面第 15 行"列严格为 month,value,mom；value 输出普通数字"，判据第 22 行钉 `'100'`、`'0'`、`'75'`（与输入 `monthly.csv` 逐字相同）。把 value 走一遍 `float()` 再 `str()` 的实现得到 `100.0`、`0.0`，同样"是普通数字"、同样满足题面，却整列判负（mom 列本身无歧义：NA/10.00/−100.00/NA/NA/50.00 已逐格复核一致）。
建议原句：**"value 按输入原样透传（整数不带小数点；补缺的月份写 0，不写 0.00）。"**

**A11　data 全族：题面写"四舍五入/固定 N 位小数"却没写舍入模式**
独立复算发现贴边值：data-008 的 SZA vwap 真值 `52.004794`，距 `.005` 半值线只差 2.1e-4；data-009 的 `0.125667`、`-0.020757` 一类三位小数取值也在舍入边界附近。当前数据下 `round()`（银行家）与 HALF_UP 结果相同，所以**本轮不影响任何判定**；但题面既然写了"四舍五入"，判据又是字符串精确比较，缺一句"按 HALF_UP，不用二进制浮点的默认舍入"就是下一次改数据即爆的隐患（data-002 的 avg、data-004 的 mom 都含除法）。
建议原句：**"两位/三位小数一律按十进制 ROUND_HALF_UP 取整（先算全精度，只在最后一步取整）。"**（fix-004 已经这样写了，可作模板。）

**A12　debug-stacktrace-001：隐藏用例把"哪一层负责无券判断"和"不得改写调用方入参"钉死，题面两处都没写**
可解性没问题（§5 的参考实现 9 可见 + 35 隐藏全绿），问题在**唯一性**：把无券判断按题面第 17 行的字面写进 `apply_coupon`（`if coupon is None: return order["total"]`）是另一种同样满足题面的实现，它 9 可见全绿、隐藏 34/35，唯一红的就是 `test_validation_layer_owns_the_rate_rules` 的 `coupon_rate(None) == 0.0`；`subtotal` 用 `item.setdefault("qty", 1)` 实现"qty 缺省为 1"也会在"不得改写调用方"那条上翻红，而题面从没写过"只读入参"。→ 判据在两层职责与入参可变性上各钉了一种实现选择。
建议原句（接题面第 17 行后）：**"无券（缺 coupon 键或 coupon 为 None）按 0 折扣处理，且该判断留在 validation 层：`coupon_rate(None)` 返回 0.0，`apply_coupon` 不得提前返回 total；`subtotal` 与 `apply_coupon` 只读入参，不得改写调用方传入的 order/item 字典，'qty 缺省为 1'是读取口径（`item.get('qty', 1)`，不是 `setdefault`）。"**

## 4. 排查过、确认干净的几类（负结果也要记账）

**4.1 隐藏用例断言异常类型**（§2.1 那一族的全面排查）：15 题共 20 处 `pytest.raises(...)`，逐条查"题面 / 可见测试 / 快照源码"三个可读位置。19 处至少在一处读得到：calc_crash 题面明写"一律 ValueError"、debug-002 可见测试里有 `IndexError`、debug-003 明写 `FileNotFoundError`、fix-004 明写 ValueError 路径、fix-006 源码自带 `TransientError/PermanentError`、debug-009/fix-009 题面点名。**唯一三处都读不到的就是 fix-008。**

**4.2 判据钉死的名字**（产物文件名、记忆 key、`files_changed` 路径、答案枚举）：数据/脚本题的产物名（`out/market_bars.csv`、`out/flow_stats.csv`、`pipeline/out/report.csv`、`run.sh`、`merge_csv.py`、`audit.py`、`report.py`、`extract_lib.py`、`release_note.md`）题面**原样写出**；记忆题钉死的 key（`profile:tenant-leak-root-cause`、`checkout:alert-window-policy`、`finance:settlement-cutoff`）不在题面里，但环境已 seed（`environments/memory_*/snapshots/<snap>/.memory_seed.json`），`memory_search` 一步就读得到，要求"更新而非新建"（`update_calls == 1 and write_calls == 0`）是合法考点；code_repo 题 `files_changed` 要求的路径在仓库里可见。**没有一题需要模型猜名字。**

**4.3 `hidden_tests.total >= N` 下限**：28 道带该门槛的题**全部核对完毕**，门槛都 ≤ 三行实跑的 pytest 收集数（debug-001 门槛 24／实跑 35，fix-004 12／32，fix-006 12／35，script-003 5／6…），**不存在"即使全过也判负"的恒假判据**。其中 12 题的门槛恰好等于当前用例数（debug-002 36、debug-005 18、debug-007 17、fix-001 32、fix-003 317、fix-005 128、script-002 6、script-006 8…）——这不是缺陷而是绊线：一旦有人删隐藏用例，该题会立刻全体判负并被发现。（我第一版这里用正则数 `def test_` 得出"9 题门槛高于实际"，是没算 `parametrize` 展开造成的假警报，已用实跑收集数重做。）

**4.4 判据引用的 state 路径是否真会被产出**：把 50 题全部 `expr` 里的 `state.*` 路径与三行实跑出来的 `state.json` 键树（逐 env 聚合）对了一遍，**没有一条判据引用了环境永不产出的字段**（唯一命中 `'funcs/state.py'` 是我正则把文件名当成了属性路径的假阳性）。这条原本是用来抓"判据恒假"的，结果为空。

**4.5 半值舍入与并列**：题面写"四舍五入"处（如 data-006）与 Python `round()` 的银行家舍入存在理论分歧，但本轮逐题核对未发现期望值恰好落在 `.xx5` 半值上；并列排名/并列排序题面均有次级排序键兜底（如 script-006:14"再按 role、path 升序"）。独立验算仍在进行，结论以 §2/§3 为已确认部分。

**4.6 反向问题：被环境预先满足的判据分支（漏区分度，不是不公平）**
memory-loop-003.yaml:26 的第三段是"三选一"的析取：`'service.json' in content or in tags or in last_correction_reason`，意在要求模型把**结论来源**记进记忆。但 seed 条目本身 `tags` 里就带着 `service.json`（`environments/memory_shell_ws/snapshots/memory-ops-003/.memory_seed.json`：`['checkout','alert','runbook','stale','service.json','metrics.csv','事故窗口','规则']`），也就是说**模型什么都不写，这一支也已经成立**。前面 `content` 必含 `Asia/Shanghai / 0.5 / 3` 那段仍然有效力，所以整题不至于失效，但"来源可追"这一考点没被真正测量。
修法只能是**收紧**（与"不许放宽"一致）：把析取改成要求 `'service.json' in content`。同族还有 memory-001 L28 那种"比提示词更宽"的计数，属于设计上的两种合法动作，不算漏。

**4.7 同一族的另外三处"恒真判据 / 不咬人的陷阱"（漏区分度，非不公平）**

| 位置 | 情况 |
|---|---|
| liberty-extract-001:57 | `inspection.preview_bytes <= 160000` 与环境上限同值，**恒真**（超限是被截断而非计数），该条不产生任何信号 |
| script-dev-008:41 | `'run.sh' in state.files` —— 快照本来就自带 `run.sh`（`snapshots/script-008/run.sh`），恒真；题面第 25–26 行"必须真的改 run.sh 并 `bash run.sh` 跑通"在确定性判据里没有对应项（只有隐藏用例的 `bash run.sh` 探针在管）。改法：`'run.sh' in state.modified` |
| clean-aggregate-001:13–14 / clean-aggregate-002:14 | 题面声明的陷阱在数据里**没有样本**：001 的"必须先去重再过滤"反序读法在本数据下同解（无"最新版被过滤、旧版可通过"的行）、`YYYY/MM/DD` 日期分支空转；002 的"非缺失但无法解析"一行都没有。→ 这是 §6"零失败面"的精确机制：**不是环境小，是声明的规则没有被数据触发** |

**4.8 独立复算/实跑通过、确认无错的（记为已核）**：data-003/005/007/008/009、clean-aggregate-001/002 的期望字面量与按题面写的独立实现**逐格相同**（含 data-007 的 18184.40：32/36 单满足 `qty×catalog = amount×0.01`、29 单满足 `settle = (amount−fee)×0.01`、0 单满足 `settle = amount×0.01`，换算系数与扣费口径两路互证）；locate-008 的 `true_value=58` 与 `run_tests` 尾行 `assert 58 == 48` 一致；fix-005 的"必须同时改两个文件"经 /tmp 实跑反证为**必要**（只修 `cfg/merge.py` 时仍有 10 条隐藏用例失败，两缺陷互相掩盖）；fix-001/002/006/007/009 的隐藏语义均能落到题面原句或可见测试上。

**4.9 debug 组其余 8 题做了参考实现级验证，全部契约完整**（脚本在 /tmp，仓库未动）：debug-002 8+36、debug-003 8+28、debug-004 8+36、debug-006 8+7、debug-007 9+17、debug-008 9+5、debug-010 9+8 全绿；debug-009 静态对照（README 十个登记点 + 题面 L18-27 覆盖 7 条隐藏用例）。其中 debug-002 的"不得就地补零/新表须新建"由题面 L14-15 明说、`IndexError` 由可见测试钉住；debug-003 的 `"y"/"t"` 落在题面"等其它字符串"的白名单口径内。

## 5. 三行全挂的两题：可解性已用参考实现证明

**debug-stacktrace-008**（历史 0/35 + 本轮 0/3，最容易被误判成"无解"）
把快照复制到 `/tmp`，**只按题面四条 bullet** 写 `review/scheduler.py`（foreground 严格优先、新 foreground 使运行中的 review 失效、同 conversation 的排队 review 剔除、其他 conversation 的 review 只留一份并空闲后恢复、迟到完成/迟到错误不改状态、retry 单一 owner）→ **14 passed（9 可见 + 5 隐藏）全绿**。
其中必须两处联动才过：可见的 `test_new_turn_removes_obsolete_review_for_same_conversation_only` 要求"同 conversation 剔除"，隐藏第 5 条要求"异 conversation 重新排一份"；题面同一句里就写了这两件事，信息是齐的。→ 题目无错；0/35 只说明它高于本次全体被测，不构成缺陷证据。

**debug-stacktrace-001**（三行分别 34/35、32/35、34/35，可见测试 9/9 全绿）
同样只按题面写参考实现（`validation.coupon_rate` + `coupon.apply_coupon` + `subtotal`）→ **9 可见 + 35 隐藏全部通过**（第一次跑只显示 9 个，是快照 `pytest.ini: testpaths = tests` 造成漏收，显式带上隐藏文件才是 44 个——这条我核对过两次才敢写）。
三行共同挂的那一条是 `test_validation_layer_owns_the_rate_rules` 的 `assert coupon_rate(None) == 0.0`：题面明写"没有券时原样返回 total"，而快照现成调用点就是 `coupon_rate(order.get("coupon"))`——无券订单每次都传 `None`。三行都按"rate 无法解析 → 报错"处理了它，属模型层误读。该隐藏文件顶部说明里也写明这条刻意要测，同时刻意**不**断言 `coupon_rate({"rate": None})` 的异常类型——正是 §2.1 那类"只考异常类"错误被提前避开的例子。
但**"可解"不等于"唯一"**：按题面第 17 行的字面把无券判断写进 `apply_coupon`（`if coupon is None: return total`）是第二种合法实现，9 可见全绿、隐藏 34/35，只红在这一条；`item.setdefault("qty", 1)` 同样会红在"不得改写调用方"那条上，而题面从未写过入参只读。这两处分层/可变性缺口记在 **§3 A12**，修法是写进题面、判据不动。

## 6. 太简单的题（9B 探针 + 内在机制）

18 题三行全过，其中三行都只用 5–8 步、且隐藏层不制造新失败面的四题：

| 题 | 27B | 9B | flash | 隐藏用例 | 环境文件 | 内在机制 |
|---|---|---|---|---|---|---|
| debug-stacktrace-003 | 6 | 7 | 7 | 13 函数／实跑 28 | 4 | 单点边界缺陷，一屏可枚举 |
| debug-stacktrace-002 | 6 | 7 | 7 | 16／实跑 36 | 4 | 两个 4 行函数，加守卫即绿 |
| fix-failing-test-001 | 6 | 8 | 5 | 18／实跑 32 | 7 | 陷阱不跨文件，可见样例即全貌 |
| script-dev-008 | 6 | 6 | 6 | ≥2 | 5 | 数据 10 行、聚合口径单一 |

`locate-bug-002~005` 是另一形状：**没有隐藏层**，判据＝四字段答案 + 证据门槛（读过指定两文件、`grep_calls >= 1`、`test_runs >= 1`、`files_changed == []`）。门槛能挡瞎猜，但不制造失败面。

内在结论与 `docs/TASK_REVIEW_20260921.md` §6 一致并更明确：**这些题不是坏了，是测不到"规模下保持正确"**。判据精确、产物必须真跑，但环境小到所有陷阱同屏可见，隐藏用例只是把可见样例换成别的数字。加难的唯一有效杠杆是**放大环境**（几百行、多类缺陷同时出现、需要 join 的第二张表），不是加判据条数——本轮 `data-007/008/009`、`debug-009/010` 的形状（9B 在这些题上 8–30 步、反复回改）已经给了对照。
独立验算还给出了更精确的一层机制（见 §4.7）：`clean-aggregate-001` 题面写"必须先去重再过滤"，可它的数据里**没有一行**能让"先去重"和"先过滤"给出不同结果；`YYYY/MM/DD` 分支、`clean-aggregate-002` 的"非缺失但无法解析"分支同样零样本。所以"环境小"只是表象，**"声明的规则没有被数据触发"才是零失败面的根因**——加难＝往数据里补触发样本，而不是往 success 里加条目。

## 7. 该记在模型账上的（不作为任何改动理由）

先划清界限：24 题"只有 9B 挂"里，有 **8 题同时撞上 §3 的 A 类缺口**（data-003 的 `order_of`/`code=`、script-002 的 Unicode 与 `--out` 目录、script-003 的 grand 口径、script-006/007、write-cli-001、liberty-001 的参数形状），这些不能算纯模型账。剔掉它们之后的净样本，抽三例说明"题面明写而它没做"：`data-pipeline-005`（题面第 16 行"金额固定两位小数"，它写 `'200'`）、`locate-bug-001`（题面第 14 行要求"通过搜索、阅读确认调用链"，`grep` 实际 0 次）、`memory-loop-002`（题面第 9–12 行要求先检索、闭环后再 done，`memory_search`/`memory_update` 各 0 次，而测试 6/6 是绿的）。

一条呈现层待办（非判分、不参与指纹）：`eval error: freq`、`eval error: library` 这类"判据求值时字段不存在"，现在与"判据算出 False"混在同一个 FAIL 里（liberty-001 两个本地行、script-002/004 的 9B）。报告与 web 应分列"缺产物"与"判据为假"，否则每次都要人工分辨。

一条 harness 隐患（会影响判分确定性，但不是题面错）：`evalkit/envs/shell_ws.py:131-137` 用 `glob("**/*.csv")` + **basename** 作 `state.csv` 的键，同名的两份 CSV 后读到的会覆盖先读到的。若解题者在 `out/` 之外留下同名产物（例如又写了一份 `report.csv`），判据取到哪一份就不确定。修法：键改成相对路径，或只采题面指定的那一条路径。

## 8. 改动清单

| 文件 | 状态 | 内容 |
|---|---|---|
| `tasks/data-pipeline/data-pipeline-006.yaml` | **已改 → 已回退**（见 §9/§10） | 题面补句一度落盘，按"可解即合格"的新尺撤回；判据自始未动，现与三行跑分时同为 `0e2a90b6` |
| `evalkit/envs/shell_hidden.py` | **已改（唯一保留的改动）** | `Scenario.prepare()` 预建每条 held-out 用例 `outs` 的父目录；只建目录，`FIXTURES`/`EXPECTED` 一字未动。8 道 shell_ws 题指纹随之变更；前后重放见 §10 |
| **未改，仅建议（判据不动，补题面原句）** | fix-008（§2.1）、fix-003（§2.4）、debug-005（§2.5）、locate-004（§2.3）、data_ws 全族（A2）、script-002/004/006（A3）、locate 全族 `symbol`（A4）、liberty-001（A5）、memory-001（A6）、script-002 Unicode（A7）、script-003 grand 口径（A8）、script-006/007/write-cli `read_file` 前置与 script-006 ts 并列（A9）、data-004 `value` 透传（A10）、data 全族舍入模式（A11）、debug-001 分层与只读入参（A12） | 每条都给了可直接粘贴的中文原句 |
| **未改，仅建议（应当收紧）** | memory-003 的 `tags` 析取分支（§4.6）、script-008 的 `'run.sh' in state.files`（§4.7）、liberty-001 的 `preview_bytes<=160000`（§4.7）；§2.5 的 debug-005 若走"删掉 state.py 那一半"也是这一档，但更推荐改成可观察要求 | 三处都被环境/上限预先满足，等于没测；收紧不影响任何已判结果 |
| harness | `evalkit/envs/shell_ws.py:131-137` CSV 键取 basename（§7） | 与题面无关，但会让同名产物下的判分不确定 |
| 判据、隐藏用例、环境、快照 | **未改** | 全套件没有任何一处判据被放宽 |

## 9. 复核修正（2026-09-22，按用户裁定重判）：标准是"理论上可解"，不是"零歧义"

用户复核后给的尺：**题目允许有干扰与误导，只要理论上可解就算合格**。上一版 §2/§3 把"判据依赖的规则没写进题面"一律当缺陷要补题面，这个口径过宽 —— 它会把"能从环境自己推出来"的难度也一并抹平。按新尺重判：

**真正的缺陷只有一种：判据依赖模型在任何可见信息里都拿不到的东西。**

- **成立的一条：script-dev-002/004/006 的 held-out `--out` 父目录**（§3 A3）。可见示例一律是 `--out out/xxx.csv`，而 held-out 跑在 `held/` 且目录不存在；模型看不到用例定义，任何"假设父目录存在"的干净实现都会 7/8 整批挂。这不是干扰，是**判据引用了环境外的事实**。→ 该补题面（或改 held-out 预建目录），二者都不降低能力门槛。

**撤回的（可解即合格，不改题面）**：
- §2.3 locate-004 那句"五个关切分别在不同模块"：`list_files` 一步就能看穿，属**合法误导**，删掉才是把题做简单。**保留原样。**
- §2.1 fix-008 的 `ValueError`、§2.4 fix-003 的分隔符全集、§3 A12 debug-001 的分层、A2/A9 的 `write_file`/`read_file` 前置、A7 的 Unicode 不规范化、A8 的 grand 口径、A10 的 `value` 透传、A4 的 `symbol` 形式、A5 的 `script=` 形状、A6 的记忆点名字段：全部是"未写但可推"（工程惯例、题面用词、随发代码的对照、或读一遍环境即知），**属难度**。27B 本轮就在 fix-008 上交卷 5/5，即证可解。**默认全部不改。**
- A11 舍入模式：本轮无一次判定受影响，只是未来改数据的隐患，不算缺陷。

**方向应当是加固，不是消歧**（与"不许变简单"同向）：
- §2.5 debug-005：问题只是判据与设题说明互相打脸（说明写"刻意不断言 state.py 形状"）。**不是放宽 gate，而是给它补一条断言 state 层隔离的隐藏用例**，让这条 files_changed 有名分；或改说明。
- §4.6/§4.7 三处恒真判据（memory-003 的 `tags` 分支、script-008 的 `'run.sh' in state.files`、liberty-001 的 `preview_bytes<=160000`）：收紧，不是可选。
- §6/§4.7 的"声明的规则没有被数据触发"：往数据里补触发样本（先去重再过滤、`YYYY/MM/DD`、非缺失但不可解析），这才是把题做难的正路。
- §7 的两条呈现层与 harness 项（eval error 与 False 分列、`state.csv` 键取 basename）与判据松紧无关，照旧。

**已落盘的 data-006 那一句按这把新尺属于可撤回项**：`round(x,2)` 写出 `576.0` 与"CSV 金额列固定两位"两种读法都成立，但 money 列固定两位是能从判据自身格式（`'576.00'` 与列名一起看）推出来的，保留原状不构成"看不到依据"。若你倾向严格贯彻"可解即合格"，我把它回退（`git checkout tasks/data-pipeline/data-pipeline-006.yaml`，判据本来就没动）。

## 10. 已执行（2026-09-22，按 §9 的新尺）

**回退**：`tasks/data-pipeline/data-pipeline-006.yaml` 已 `git checkout` 回原状（题面恢复"四舍五入到两位小数"，指纹回到 `0e2a90b6`，与三行跑分时的版本重新同尺）。§2.2 的分析保留作记录，但**不作为改动**。

**唯一真缺陷已修**：`evalkit/envs/shell_hidden.py` 的 `Scenario.prepare()` 现在为每条 held-out 用例**预建 `outs` 的父目录**（只建目录；`FIXTURES`/`EXPECTED`/`outs` 一字未改，产物内容判据没有放宽）。理由就是 §9 的那把尺：这条要求依赖模型看不到的事实。

前后对照（同一条轨迹灌回当前判据，`replay_gate --runs` 限定三行）：

| 题 | 9B 隐藏用例 BEFORE | AFTER | 27B / flash（原本判对） |
|---|---|---|---|
| script-dev-002 | 0/6 | 4/6 | 6/6 → 6/6 |
| script-dev-004 | 0/6 | 1/6 | 6/6 → 6/6 |
| script-dev-006 | 0/8 | 7/8 | 8/8 → 8/8 |

也就是说：目录这一层把 9B 的真实成绩从"整批 0 分"遮住了——修完之后它不再是"看不见目录"输的，而是输在自己的实现上（script-006 剩下的 1 条正是坏记录 JSON 让 CLI 崩掉，题面第 19 行明写不允许）。**判对→判判错的翻转：0 例**（三行两条已判对的轨迹重放后仍 6/6、8/8）；全历史 `--only-passed` 回归重放在跑，结果补在这里。

代价：`shell_hidden.py` 在指纹内（fingerprint.py:48-49），所以 **8 道 shell_ws 题全部换指纹**（script-dev-002~008、write-cli-001），这些题的历史成绩与新版本不再同尺 —— 判分语义确实变了，这是应有的代价。`tools/test_infra_retry.py` 37/37 通过。

### 10.1 全量回归对照（`replay_gate --only-passed`，只回放历史判对的老轨迹）

同一批老轨迹，在有/无我这 7 行两种状态下各跑一遍（八题全量，样本 27–33 条/题）：

| 题 | 历史判对 | BEFORE（无我的改动）仍判对 | AFTER（含改动）仍判对 |
|---|---|---|---|
| script-dev-002 | 28 | 18 (64%) | 18 (64%) |
| script-dev-003 | 31 | 3 (10%) | 3 (10%) |
| script-dev-004 | 32 | 14 (44%) | 14 (44%) |
| script-dev-005 | 31 | 11 (35%) | 11 (35%) |
| script-dev-006 | 27 | 10 (37%) | 10 (37%) |
| script-dev-007 | 2 | 2 | 2 |
| script-dev-008 | 3 | 3 | 3 |
| write-cli-001 | 33 | 17 (52%) | 17 (52%) |

**八题逐题相同 → 我的改动翻转了 0 例"历史判对"**（方向上也只可能变好：它只补目录，不动 `FIXTURES`/`EXPECTED`/`outs`）。
差值只出现在本来判错的轨迹上：9B 在 script-002/004/006 的隐藏用例从 0/6、0/6、0/8 变成 4/6、1/6、7/8 —— 也就是它此前的"全军覆没"有一部分是目录造成的假象，修完之后剩下的才是它自己的错。

**顺带记一条与我的改动无关、但值得单独看一眼的事实**：这批老轨迹之所以大面积不再判对，是因为这几题的 hidden 用例此前被加难过（用例数与期望都变严），按今天的尺重算自然掉。其中 **script-dev-003 只剩 3/31（10%）**，已在 20–60% 标定带之下；它的 6 个 case 输出路径是题面明写的 `out/report.csv`，目录这一层我这次也一并预建了却一条都没救回来（BEFORE=AFTER），说明死因是产物内容不符，属"更难了"而不是"看不见依据"。要不要把它拉回带内是加难尺度的问题，不是缺陷问题。
