# AgentBench-Local：Agent 大模型能力评测系统设计文档

> 目标：评测大模型在 **Agent 场景**下的真实能力——不是"答得对不对"，而是"能不能在可交互环境中多步把事办成"。
> 环境策略：Mock / 沙箱（确定性强、可复现、安全、不烧钱）。
> 被测对象：云端与本地 OpenAI 兼容模型（当前包含 127.0.0.1:1235、127.0.0.1:8080）统一接入。
> 裁判：**可选闸门 + 多把可选的尺**。`models.yaml` 的 `judge.profiles` 是档案表、`judge.default`
> 是不点名时的那把（现默认 `mimo-flash` = MiMo v2.6 flash，`MIMO_API_KEY`）；历史那批跑分用的
> `luna-6` = `gpt-6-luna`（Responses API，example.com，`EXAMPLE_API_KEY`）仍在表里，点名即可继续用。
> 任务集：基于真实 Codex 会话、`local-llama` 复杂工程案例与项目内真实 67MB Liberty 文件，共 **40 题**，当前均为 hard。
> 其中 locate 7 题、fix-and-test 8 题、debug 8 题、data-pipeline 7 题、script-dev 6 题、memory-loop 4 题。
> manifest 的 task fingerprint 同时覆盖题目规范、snapshot、隐藏测试和 evaluator/agent-loop 语义；任一项变化后旧 run 不进入当前排行榜。

代码题和脚本题在无网络、只挂载单题工作区的 Docker 容器内执行。首次运行前构建代码题测试镜像：

```bash
docker build -t agent-eval-pytest:3.12 -f docker/code-tests.Dockerfile .
```

---

## 1. 设计目标与核心原则

### 1.1 评测什么

聚焦 **Agent 能力**，拆成 5 个可独立打分的子维度：

| 维度 | 定义 | 典型任务形态 |
|------|------|--------------|
| **工具调用 (tool-calling)** | 能否选对工具、填对参数、解析返回 | 单步/少量步骤，查数据后执行 |
| **多步规划 (planning)** | 能否拆解长任务、编排执行顺序 | 需要 3+ 步、有依赖关系的任务 |
| **错误恢复 (recovery)** | 遇到工具报错/返回异常时能否纠错重试 | 预埋一次失败，看是否换路 |
| **状态追踪 (state-tracking)** | 能否记住并利用中间状态，不重复/不遗漏 | 需要跨步骤保持信息一致 |
| **约束遵循 (constraint)** | 能否满足硬约束（预算、步数、格式、安全边界） | 带限制条件的任务 |

每个任务可以标注 1~多个维度，报告层按维度聚合，就能看出模型强在哪一环、弱在哪一环。

### 1.2 核心原则（决定整个系统质量的红线）

1. **成功条件与轨迹解耦**：验证的是**环境最终状态**，不是模型"嘴上说的"。杜绝"说已下单但没下单"式骗分。
2. **可复现**：固定 seed、环境跑前重置到快照、记录全部随机源。两次跑同一模型同一任务，结果必须可比。
3. **确定性优先**：能用规则/状态断言/单测判的，绝不用 LLM-judge。LLM-judge 只兜底开放题。
4. **有成本边界**：每任务强制 `max_steps` / token 预算 / 超时，防止 agent 绕圈烧钱烧时间。
5. **可扩展**：任务、环境、验证器都是插件式，加一个新任务不碰框架代码。

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  报告层  Report                                                │
│  指标聚合 / 模型横向对比 / 维度雷达 / 失败归因 / 导出            │
├─────────────────────────────────────────────────────────────┤
│  评分层  Evaluator                                             │
│  验证器组合：状态断言 | 单元测试 | 规则检查 | LLM-judge          │
│  输出 EvalResult（分维度得分 + 通过/失败 + 证据）                 │
├─────────────────────────────────────────────────────────────┤
│  执行层  Runner                                                │
│  ① 重置环境→initial_state                                      │
│  ② agent loop：喂任务→模型思考→工具调用→回填观察→循环             │
│  ③ 全程记录 Trajectory（thought/tool/obs/step/token/latency）  │
│  ④ 终止条件：模型主动结束 | 达到 max_steps | 超时                │
├─────────────────────────────────────────────────────────────┤
│  环境层  Environment（Mock / 沙箱）                             │
│  工具服务（mock API）/ 文件系统 / 数据库 快照 + 重置              │
│  隔离：容器或进程沙箱，防止危险操作                               │
├─────────────────────────────────────────────────────────────┤
│  任务层  Task Spec（YAML）                                      │
│  声明任务、环境、初始状态、可用工具、成功条件、预算、难度、标签     │
└─────────────────────────────────────────────────────────────┘
```

数据流：

```
Task Spec ──► Runner ──(驱动)──► Model(被测)
                │                    │
                │  读/写状态          │ 产出
                ▼                    ▼
          Environment  ◄────────  Trajectory
                │                    │
                └────────► Evaluator（状态断言读 Environment 终态）
                              │
                              ▼
                          EvalResult ──► Report
```

---

## 3. 核心数据模型

### 3.1 Task Spec（YAML，一个任务一个文件）

```yaml
id: fix-failing-test-001            # 全局唯一
title: 修好 failing test
category: fix-and-test              # 主维度
tags: [planning, constraint]        # 附加维度（一个任务可考多环）
difficulty: medium                  # medium | hard（本任务集不设 easy）

environment:
  name: code_repo                   # 指向 environments/code_repo
  snapshot: calc_buggy.tar          # 环境初始状态快照（可复现关键）

agent:
  system_prompt: |
    你是工程师，只能用提供的工具修改代码。
    改完必须用 run_tests 验证，然后调用 done 结束。
  tools: [read_file, grep, edit_file, run_tests, done]

task_prompt: |
  让 tests/test_calc.py 里的所有测试通过。

budget:
  max_steps: 20                     # agent 最多走多少轮（一轮=一次模型调用+工具）
  max_tokens: 12000                 # 总 token 预算
  timeout_s: 420                    # 墙钟超时

success:
  # 多个条件，默认全部满足才 pass（见 4.2 逻辑）
  - type: state_assert
    all_of:
      - "test_report.failed == 0"   # 测试全绿（客观硬指标）
      - "test_report.passed >= 5"
  - type: tool_used                 # 过程约束：必须真的跑过测试
    name: run_tests
  - type: no_tool                   # 负约束：不许删测试作弊
    name: delete_test
```

### 3.2 Environment（环境）

环境是一组**带状态、可重置、可断言**的服务/资源的集合。

```python
# 环境需实现的接口（Python 伪代码）
class Environment:
    def reset(self, snapshot: str) -> None: ...   # 从快照恢复初始状态
    def tools(self) -> list[ToolSpec]: ...        # 返回可用工具的 schema
    def call_tool(self, name: str, args: dict) -> ToolResult: ...  # 执行工具，改变内部状态
    def state(self) -> dict: ...                  # 导出当前状态快照（供断言/归因）
    def teardown(self) -> None: ...
```

**Mock 服务实现要点**
- 每个环境是一个独立的 mock 服务（本地起 HTTP 或进程内调用），工具 = 它的 API 端点。
- `state()` 返回结构化 dict，断言器直接对它求值（推荐用表达式引擎，如 Python 受限求值或 jsonpath/CEL）。
- 重置 = 用 `snapshot` JSON 重新载入，保证每次任务起点一致。
- **预埋错误**：某环境可配置"第 N 次调用某工具返回特定错误"，用于考 recovery 维度。

### 3.3 Trajectory（轨迹）— 归因的命脉

```json
{
  "task_id": "fix-failing-test-001",
  "model": "qwen3-27b-local",
  "seed": 42,
  "started_at": "2026-08-21T10:00:00Z",
  "steps": [
    {
      "idx": 0,
      "thought": "test_sum 挂了，先读测试和 calc.py 定位。",
      "tool_calls": [
        {"name": "read_file", "args": {"path": "src/calc.py"}}
      ],
      "observations": [
        {"name": "read_file", "ok": true, "result": {"content": "def sum(...): ..."}}
      ],
      "tokens": {"in": 120, "out": 34},
      "latency_ms": 850
    }
    // ... 每一步都这样记录
  ],
  "termination": "done_called",        // done_called | max_steps | timeout | error
  "total_steps": 5,
  "total_tokens": 1540,
  "elapsed_s": 4.2,
  "final_state_digest": {...}          // 环境终态摘要（存快照引用，不内联大对象）
}
```

> 存盘策略：每任务一个 `trajectory.json`（步骤明细）+ 环境终态快照文件。大 observation 用引用，不内联。

### 3.4 EvalResult（评测结果）

```json
{
  "task_id": "fix-failing-test-001",
  "model": "qwen3-27b-local",
  "passed": true,
  "scores": {                          // 按维度给分 0~1
    "tool-calling": 1.0,
    "planning": 0.8,
    "constraint": 1.0
  },
  "checks": [
    {"type": "state_assert", "expr": "test_report.failed == 0", "passed": true, "evidence": "failed=0"},
    {"type": "tool_used", "name": "run_tests", "passed": true},
    {"type": "budget", "limit": "max_steps<=15", "actual": 5, "passed": true}
  ],
  "efficiency": {"steps": 5, "tokens": 1540, "elapsed_s": 4.2},
  "failure_reason": null,              // 失败时归因：哪个 check 挂了 / 轨迹哪一步偏了
  "trajectory_ref": "runs/20260821/fix-failing-test-001/trajectory.json"
}
```

---

## 4. 任务设计规范（决定评测质量的核心）

### 4.1 目录组织

```
tasks/
  locate/                # 代码定位（读+grep+理解结构，追调用链/反向依赖）
    locate-bug-001.yaml          # M
    locate-callchain-002.yaml    # H
    locate-symbol-003.yaml       # M
    locate-impact-004.yaml       # H
    locate-redherring-005.yaml   # H
    locate-bug-006.yaml          # H：跨层调用与缓存键污染
  fix-and-test/          # 改代码跑测试（改后 pytest 全绿，你最高频）
    fix-failing-test-001.yaml    # M
    fix-runtime-error-002.yaml   # M
    refactor-keep-green-003.yaml # H
    fix-boundary-004.yaml        # H
    fix-regression-005.yaml      # H
    fix-failing-test-006.yaml    # H：重试异常边界与指数退避
    fix-failing-test-007.yaml    # H：分支会话最长精确 checkpoint
  debug/                 # 排障修复（复现→定位→修复，不回归）
    debug-stacktrace-001.yaml    # M
    debug-config-002.yaml        # M
    debug-silent-003.yaml        # H
    debug-flaky-004.yaml         # H
    debug-hang-005.yaml          # H
    debug-stacktrace-006.yaml    # H：TTL 单位与假值缓存语义
    debug-stacktrace-007.yaml    # H：checkpoint 恢复后的异步 MTP 生命周期
  data-pipeline/         # 数据 pipeline（清洗/聚合脏数据，输出断言对）
    clean-aggregate-001.yaml     # M
    dedup-join-002.yaml          # M
    impute-anomaly-003.yaml      # H
    multi-source-merge-004.yaml  # H
    incremental-005.yaml         # H
    data-pipeline-006.yaml       # H：版本去重、时点汇率与净收入
  script-dev/            # 脚本/工具开发（满足约束、真实执行、行为对）
    write-cli-001.yaml           # M
    write-parser-002.yaml        # M
    write-robust-003.yaml        # H
    write-perf-004.yaml          # H
    write-idempotent-005.yaml    # H
    script-dev-006.yaml          # H：时区、去重、关联与双数据集 CLI
```

### 4.2 成功条件设计（解耦原则落地）

三类条件可组合：

1. **终态断言 (state_assert)** —— 最重要。对环境最终状态求值，客观、可复现。单条件用 `expr:`，多条件用 `all_of:`。
   ```yaml
   - type: state_assert
     all_of:
       - "test_report.failed == 0"         # 测试全绿
       - "files_changed contains 'calc.py'" # 确实改了目标文件
   ```
2. **过程约束 (tool_used / no_tool / order_of)** —— 考"有没有真的做"、"顺序对不对"。
   ```yaml
   - type: tool_used
     name: run_tests                      # 必须真的跑过测试
   - type: order_of
     tools: [read_file, edit_file, run_tests]   # 先读→改→测
   ```
3. **预算约束 (budget)** —— 步数/token/时间上限，超限记"效率失败"（任务可能完成但扣效率分）。

判定逻辑：
- **硬条件**（终态 + 过程）全过 → `passed = true`。
- **预算条件**单独记"效率分"，不影响 pass/fail，但在报告里体现"做成了但很费劲"。

### 4.3 难度分层（横向可比的前提）

> 当前正式任务集不设 easy/medium，**38 题全部为 hard**。旧版 `001-005` 已原位升级，历史通过率须结合 manifest fingerprint 解读。

| 难度 | 特征 | 示例 |
|------|------|------|
| medium | 3~6 步，有依赖，1 个约束 | 修 1 个 failing test：读→定位→改→跑测试全绿 |
| hard | 7+ 步，需纠错、跨步状态、多约束、干扰项 | 多文件追调用链定位 bug / 修回归且不破坏其它测试 / flaky 多轮复现 |

报告层按难度分桶统计通过率，能看出模型能力边界在哪一档。

### 4.4 完整任务示例（3 个代表题，展示不同验证类型）

以下片段仅展示 Task Spec 的不同验证写法；当前正式题目的准确内容以 `tasks/` 下 YAML 为准，完整分布见 4.5。

**示例 A：fix-and-test / hard（回归约束）**
```yaml
id: fix-regression-005
title: 修复回归且不破坏其它测试
category: fix-and-test
tags: [planning, constraint]
difficulty: hard
environment:
  name: code_repo
  snapshot: calc_regression.tar     # 一次改动让 1 个测试挂，其余 9 个原本绿
agent:
  tools: [read_file, grep, edit_file, run_tests, done]
  system_prompt: "修 bug。不要破坏原本通过的测试。改完必须 run_tests。"
task_prompt: "CI 红了：test_invoice 失败。修好它，并保证其它测试仍全绿。"
budget: {max_steps: 25, max_tokens: 16000, timeout_s: 600}
success:
  - type: state_assert
    all_of:
      - "test_report.failed == 0"      # 目标测试修好
      - "test_report.passed == 10"     # 无回归：10 个全绿
  - type: tool_used
    name: run_tests
    min_calls: 2                       # 改前/改后都要跑测试验证
```

**示例 B：debug / hard（偶发复现）**
```yaml
id: debug-flaky-004
title: 定位并修复 flaky（偶发）缺陷
category: debug
tags: [recovery, state-tracking]
difficulty: hard
environment:
  name: code_repo
  snapshot: calc_flaky.tar           # 并发/时序 bug：同一输入有时对有时错
agent:
  tools: [read_file, grep, edit_file, run_tests, run_target, done]
  system_prompt: "偶发问题，多次运行观察。修到稳定为止。"
task_prompt: "process_orders 偶尔丢单，时好时坏。找出根因并修到 10 次运行全部正确。"
budget: {max_steps: 30, max_tokens: 20000, timeout_s: 900}
success:
  - type: state_assert
    all_of:
      - "run_target.successes == 10"   # 连跑 10 次全对（稳定性断言）
  - type: tool_used
    name: run_target
    min_calls: 3                       # 必须真的多次复现，不能一次蒙对
```

**示例 C：data-pipeline / medium（数据断言）**
```yaml
id: clean-aggregate-001
title: 清洗脏数据并按月聚合
category: data-pipeline
tags: [constraint]
difficulty: medium
environment:
  name: data_ws
  snapshot: sales_dirty.csv          # 含重复、缺失、金额格式错误
agent:
  tools: [read_file, run_python, list_files, done]
  system_prompt: "写并运行 Python 处理数据，结果写到 out/result.csv。"
task_prompt: "清洗 sales_dirty.csv（去重、填缺失、统一金额），按月聚合成交额到 out/result.csv。"
budget: {max_steps: 18, max_tokens: 12000, timeout_s: 480}
success:
  - type: state_assert
    all_of:
      - "out.result.rows == 12"                      # 12 个月
      - "out.result['2026-01'].amount == 18234.56"   # 抽查关键月
      - "out.result.no_nulls == true"
  - type: tool_used
    name: run_python
```

### 4.5 任务清单（40 题全量）

每题一个独立 YAML、snapshot 与确定性成功断言。所有正式题均为 hard。

| 类别 | 数量 | id 范围/名称 | 主要考点 |
|------|----:|--------------|----------|
| locate | 7 | `locate-bug-001`～`007` | 跨层调用链、干扰项、多根因泄漏审计与真实阅读证据 |
| fix-and-test | 8 | `fix-failing-test-001`～`008` | 多缺陷修复、精确契约、重试与 checkpoint 链策略 |
| debug | 8 | `debug-stacktrace-001`～`008` | 静默错误、共享状态、异步 MTP 与后台 review 重入 |
| data-pipeline | 7 | `clean-aggregate-001/002`、`data-pipeline-003`～`006`、`liberty-extract-001` | 版本去重、SCD、确定性聚合、67MB Liberty 有界探查 |
| script-dev | 6 | `write-cli-001`、`script-dev-002`～`006` | 双输入 CLI、Unicode、时区、日志与精确输出 |
| memory-loop | 4 | `memory-loop-001`～`004` | 通用规范的主动触发、记忆调用、证据核验与错误记忆原位修正 |

---

## 5. 环境层设计

### 5.1 Mock 服务的形态

两种实现，按隔离强度选：

| 方案 | 适用 | 优点 | 缺点 |
|------|------|------|------|
| **进程内 mock**（Python 对象模拟工具） | 起步最快 | 简单、快、好调试 | 隔离弱，复杂工具不好模拟 |
| **本地容器/HTTP mock 服务** | 正式评测 | 真实 API 语义、强隔离、可重置 | 需写服务、起容器 |

建议：**起步用进程内 mock 快速跑通**，任务变复杂（要真实 HTTP 语义、文件、DB）时迁到容器方案。两种都实现 `Environment` 接口，对上层透明。

### 5.2 状态快照与重置

- 每个环境有一个初始快照（`.tar`/目录），描述初始世界（代码仓文件树+内容、脏数据集、环境配置等）。
- `reset(snapshot)` = 清掉沙箱目录 + 重新铺快照，保证每次任务起点一致。
- 预埋错误用快照里的 `bug` 配置驱动（如"calc.py 有个 off-by-one""某依赖首次调用抛异常"）。

### 5.3 隔离与安全

- 进程内 mock：天然隔离（只操作自己的内存状态）。
- 容器方案：每任务一个一次性容器，跑完销毁；不给危险权限；只暴露 mock 端口。
- 模型只能看到 `agent.tools` 里声明的工具，无法越权访问别的资源。

---

## 6. 执行层（Runner）设计

### 6.1 模型接入（provider 抽象）

**关键洞察**：被测的云端和"本地 Qwen3.8"（127.0.0.1:1235）都是 **OpenAI 兼容协议**，只是 `base_url` 不同，统一走 `chat.completions.create(messages, tools)`。裁判 `luna`（`gpt-5.6-luna` @ example.com）走的是 **OpenAI Responses API**（`/responses`）。所以 provider 需支持两种 wire_api（`chat` / `responses`），用 [litellm](https://github.com/BerriAI/litellm) 或自建薄封装。

```python
class ModelProvider:
    def __init__(self, config: dict): ...   # name, base_url, api_key, model, temperature, seed
    def chat(self, messages: list, tools: list[ToolSpec]) -> ChatResponse: ...
    # ChatResponse: content, tool_calls[], usage{in,out}
```

模型配置文件：
```yaml
# models.yaml
# 被测模型（wire_api: chat = OpenAI chat/completions 协议）
models:
  - name: qwen3-27b-local
    base_url: http://127.0.0.1:1235/v1
    api_key: "local"
    model: local
    wire_api: chat
    temperature: 0.0        # 评测用 0，保证可复现
    seed: 42
  - name: gpt-4o            # 或任意 OpenAI 兼容云端模型
    base_url: https://api.openai.com/v1
    api_key: "${OPENAI_API_KEY}"
    model: gpt-4o
    wire_api: chat
    temperature: 0.0
    seed: 42

# 裁判：一组命名档案 + 一个默认档；不想跑裁判就 --no-judge / 面板选「不启用」
judge:
  default: mimo-flash
  profiles:
    mimo-flash:
      base_url: https://api.xiaomimimo.com/v1
      api_key: "${MIMO_API_KEY}"
      model: mimo-v2.6-flash
      wire_api: chat
      stream: true
    luna-6:
      name: luna
  base_url: https://example.com/v1
  api_key: "${EXAMPLE_API_KEY}"
  model: gpt-5.6-luna
  wire_api: responses
  temperature: 0.0
```

### 6.2 Agent Loop

```
messages = [system_prompt, task_prompt]
for step in range(1, max_steps + 1):
    resp = provider.chat(messages, tools)
    traj.steps.append(thought=resp.content, tool_calls=resp.tool_calls)

    if resp.tool_calls 里有 done:
        记录 termination=done_called; break

    for tc in resp.tool_calls:
        obs = env.call_tool(tc.name, tc.args)
        traj.steps[-1].observations.append(obs)
        messages.append(assistant_msg(resp))
        messages.append(tool_result_msg(tc, obs))

    check token/timeout budget → 超了就 break
```

要点：
- **一轮 = 一次模型调用**（可能带多个并行工具调用）。
- `done` 工具是约定的结束信号；模型不调 done 就靠 `max_steps`/超时兜底。
- 每步记 `tokens`、`latency_ms`，进效率统计。
- 温度 0 + seed 固定 → 可复现。

### 6.3 终止与边界

- 正常结束：模型调 `done`。
- 强制终止：`max_steps` / `max_tokens` / `timeout_s` 任一触发。
- 异常：工具崩溃/超时 → 记 `termination=error`，该任务判 fail 并归因。

---

## 7. 评分层（Evaluator）设计

### 7.1 验证器（Verifier）

每个成功条件是一个 Verifier，统一接口：

```python
class Verifier:
    def evaluate(self, traj: Trajectory, final_state: dict) -> CheckResult: ...
    # CheckResult: passed(bool), evidence(str)
```

| Verifier | 输入 | 判定 |
|----------|------|------|
| `StateAssert` | 环境终态 `final_state` | 对表达式求值（jsonpath / CEL / 受限 Python） |
| `ToolUsed` | trajectory | 某工具被调用次数 ≥ min_calls |
| `NoTool` | trajectory | 某工具从未被调用 |
| `OrderOf` | trajectory | 工具调用顺序符合预期 |
| `Budget` | trajectory | steps/tokens/elapsed 在上限内（单独记效率） |
| `LLMJudge` | 任务 + 轨迹 + rubric | 开放题兜底（见 7.3） |

### 7.2 评分聚合

- **任务级 pass/fail**：所有**硬条件**（state_assert / tool_used / no_tool / order_of）全过。
- **维度得分**：把该任务考的各维度，按其关联 check 的通过率折算成 0~1。
- **效率分**：预算类 check 单独算，体现"做成 vs 做费劲"。
- **模型级报告**：按维度、按难度分桶聚合所有任务的得分/通过率/效率。

### 7.3 LLM-as-judge（只兜底开放题，需校准）

- **裁判是一把可换的尺，不是一个写死的端点**：`judge.default` 决定不点名时用哪把（现默认
  `mimo-flash`），CLI `--judge [档案名]` 点名、`--no-judge` 关闭，面板是一个下拉框（含「不启用」）。
- **换尺 = 换分母**：题面用 `judge_id`（`档案名:model`）钉死它认的那把，对不上就记「未评」、
  不进通过率分母；现在 7 道 rubric 题钉的是 `luna-6:gpt-6-luna`，用默认档跑它们就会全部未评。
- 必须给 **rubric**（明确评分维度 + 锚点示例）+ **few-shot** 标注样例。
- **校准**：抽 10~20 个任务人工标注，算裁判与人工的一致率（Cohen's κ 或相关系数），低于阈值要调 rubric。
- 裁判结果**必须**连同 rubric、原始输出一起存档，可复查、可重跑。

---

## 8. 报告层设计

### 8.1 指标体系

| 指标 | 含义 |
|------|------|
| **Pass@1** | 单次跑通过率（主指标） |
| **Pass@k** | 跑 k 次任一次通过（看稳定性，可选） |
| **维度得分** | tool-calling / planning / recovery / state-tracking / constraint 各维度 0~1 |
| **难度分桶通过率** | easy / medium / hard 各档通过率（看能力边界） |
| **效率** | 平均步数、平均 token、平均耗时 |
| **稳定性** | 同任务多次跑的 pass 方差 |

### 8.2 视图

1. **模型对比表**：多模型 × 各维度/难度，一张表横向比。
2. **维度雷达图**：单模型的能力画像。
3. **难度曲线**：难度越高通过率掉多少，定位能力上限。
4. **失败归因页**：点进某个失败任务，看 trajectory 逐步回放 + 哪个 check 挂了 + 卡在第几步。
5. **导出**：JSON（原始）+ Markdown/HTML（人读）+ CSV（进表格）。

---

## 9. 工程关键问题清单

| 问题 | 对策 |
|------|------|
| **可复现** | 温度 0 + seed 固定 + 环境快照重置 + 记录全部随机源 |
| **成本控制** | 每任务强制 budget；批量跑前先跑 easy 档冒烟 |
| **防止"答对没做成"** | 只看环境终态断言，不看模型输出文本 |
| **agent 绕圈** | max_steps / timeout 硬兜底 |
| **可扩展** | 任务/环境/验证器全插件化，加任务零改框架 |
| **安全** | 沙箱隔离 + 工具白名单 |
| **裁判偏差** | LLM-judge 需人工校准，确定性优先 |
| **大数据量** | trajectory 分页存盘，终态用引用不内联 |

---

## 10. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 语言 | **Python 3.11+** | 生态成熟，litellm/pydantic/pytest 全有 |
| 多模型接入 | **litellm**（或自建薄封装） | 统一 OpenAI 兼容协议，云端+本地一致 |
| 数据校验 | **pydantic** | Task Spec / Trajectory / EvalResult 强类型 |
| 配置 | **YAML + pydantic** | 任务/模型声明式、可读、好 diff |
| 状态断言 | **jsonpath-ng** 或 **CEL** 或受限 Python eval | 对终态表达式求值 |
| 单测类任务 | **pytest** 子进程 | 代码任务跑真实测试 |
| 沙箱（进阶） | **Docker** | 强隔离，一次性容器 |
| 报告 | **Jinja2 模板** → HTML/MD + 简单图表（matplotlib） | 轻量，够用 |
| 并发 | **asyncio / 线程池** | 多模型并行跑，提速 |

> 起步最小依赖：`litellm + pydantic + pyyaml + jsonpath-ng`，先把 10 个任务跑通。

---

## 11. 仓库目录结构

```
agent-eval/
├── docs/
│   └── DESIGN.md                  # 本文档
├── tasks/                          # 任务定义（一个任务一个 yaml）
│   ├── locate/                     # 代码定位
│   ├── fix-and-test/               # 改代码跑测试
│   ├── debug/                      # 排障修复
│   ├── data-pipeline/              # 数据 pipeline
│   └── script-dev/                 # 脚本/工具开发
├── environments/                   # 沙箱环境实现
│   ├── base.py                     # Environment 接口
│   ├── code_repo/                  # 代码仓沙箱（读/grep/编辑/跑 pytest）
│   │   ├── env.py
│   │   ├── snapshots/
│   │   │   └── calc_buggy.tar      # 含预埋 bug 的代码仓快照
│   │   └── verify.py
│   ├── data_ws/                    # 数据工作区沙箱（脏数据 + 运行 Python）
│   └── shell_ws/                   # 脚本/命令行沙箱（write/run_bash）
├── runner/
│   ├── provider.py                 # ModelProvider（litellm 封装）
│   ├── agent_loop.py               # agent loop + 终止/边界
│   └── trajectory.py               # 轨迹记录
├── evaluator/
│   ├── base.py                     # Verifier 接口
│   ├── state_assert.py
│   ├── tool_flow.py                # ToolUsed / NoTool / OrderOf
│   ├── budget.py
│   ├── llm_judge.py
│   └── aggregate.py                # 维度/难度/效率聚合
├── report/
│   └── render.py                   # 生成对比表/雷达/归因页
├── models.yaml                     # 被测模型配置
├── cli.py                          # 入口：run / report
├── runs/                           # 每次运行产物（trajectory + 终态 + EvalResult）
└── requirements.txt
```

---

## 12. CLI 使用形态（设计目标）

```bash
# 跑单任务，指定被测模型
python cli.py run --task tasks/fix-and-test/fix-failing-test-001.yaml --model qwen3-27b-local

# 跑某个场景 / 全部任务，多模型并行（裁判默认开着，用 judge.default 那把尺）
python cli.py run --suite tasks/fix-and-test --models qwen3-27b-local,gpt-4o --parallel 4

# 生成对比报告
python cli.py report --run runs/20260821 --out report.html

# 复现某次失败（重跑单任务，固定 seed）
python cli.py run --task tasks/debug/debug-hang-005.yaml --model gpt-4o --seed 42
```

---

## 13. 落地路线图（分阶段，先跑通再扩张）

**Phase 1 — 最小可跑框架（已完成）**
- [x] Task Spec / Trajectory / EvalResult、provider、agent loop 与结果落盘
- [x] code/data/shell/large-file/memory workspace 环境
- [x] StateAssert、ToolUsed、NoTool、OrderOf 验证器

**Phase 2 — 对比与报告（主体完成）**
- [x] 多模型运行、报告表、维度聚合、trajectory/state 回放
- [x] 40 道 hard 任务与 task/suite fingerprint
- [ ] 进一步增加多模型并行调度和失败归因可视化

**Phase 3 — 评分增强（按需）**
- [x] 接 LLM-judge（luna，OpenAI Responses API）并保留确定性 verifier 主判定
- [x] 扩充 script-dev / data-pipeline 与 hard 多文件排障任务
- [x] 加入大文件有界探查和记忆闭环任务
- [ ] Pass@k、稳定性统计

**Phase 4 — 工程化（按需）**
- [x] 代码与脚本执行迁到无网络、单工作区挂载的 Docker 沙箱
- [ ] 任务集规模化（50+）
- [x] 可视化仪表盘、运行控制台、历史与模型排名
- [ ] 结果数据库化，支持历史趋势

---

## 14. 当前状态与下一步

**已确认**
- **任务集** = 7 类 **38 题**，全部 hard；旧版简单题已原位升级。
- **裁判** = 可选闸门，默认档 `mimo-flash`（MiMo v2.6 flash，`MIMO_API_KEY`）；`luna-6`
  （`gpt-6-luna`，`EXAMPLE_API_KEY`）仍是与历史那几行同尺的选项。
- **被测** = 本地模型（127.0.0.1:1235、127.0.0.1:8080）+ 可选云端模型。

**下一步**
1. 用 8080 本地模型跑完整 38 题并保留 manifest fingerprint。
2. 根据通过率、步骤数与失败轨迹校准题目区分度，不因弱模型低于 50% 而降低硬约束。
3. 后续增加 Pass@k、稳定性统计与可视化失败归因。
