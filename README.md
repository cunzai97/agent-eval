# agent-eval

在**真实 coding agent harness** 里跑任务、按金标准判分的评测套件。不是"问模型一道题"的问答基准：每个任务都是一份可执行的工作目录（数据文件、仓库快照或无状态 shell 环境），被测模型用工具去读、改、跑，最后由确定性断言和 rubric 两道闸门判分。

## 特点

- **任务即环境**：`tasks/*.yaml` 描述目标与判据，`environments/` 提供快照，工具集固定（读写文件、跑 python/bash、记忆读写等）。
- **两道判分闸门**：确定性断言（state_assert / tool_used / order_of / hidden_tests）管大多数题；只有断言够不到的开放题才交给 rubric 裁判。
- **只补判决，不重采样**：`rejudge` 拿已归档的轨迹重跑裁判闸门，不重新调用被测模型——裁判故障不会浪费一次采样。
- **判分口径进指纹**：`evalkit/fingerprint.py` 把任务定义、环境实现、判分代码一起哈希。题面或判分逻辑一动，旧分数就对不上，避免新旧成绩混在一张榜上。
- **失败可归因**：每条结果都留 `failure_reason`，区分"模型没做对"和"上游/裁判故障"，后者记未评、不计入通过率分母。

## 快速开始

```bash
pip install -r requirements.txt
cp models.example.yaml models.yaml      # 填入你自己的端点；密钥用 ${ENV_VAR} 引用
export EXAMPLE_API_KEY=...              # 或写进 .env（已在 .gitignore）

python3 cli.py run --models local-qwen --out runs/myrun --jobs 1
python3 cli.py report --run runs/myrun
```

`run` 默认跑 `--bank hard40`（40 题）；传 `--bank ''` 不限题库，`--tasks a,b` 可按题号或维度过滤。

其他子命令：

| 命令 | 作用 |
|---|---|
| `run` | 跑评测（默认 hard40 题库，job 1） |
| `rejudge` | 只重跑裁判闸门（`--judge <档案>`、`--tasks <id>`） |
| `report` | 从 run 目录生成 Markdown 报告 |
| `history` | 跨 run 的历史对比 |
| `banks` | 列出可用题库 |

## 目录

```
cli.py               命令行入口
evalkit/             执行、判分、指纹、重判、各环境实现
tasks/               题目定义（7 个维度：data-pipeline / debug / fix-and-test /
                     judge / locate / memory-loop / script-dev）
environments/        任务用的沙箱快照与隐藏测试
banks/               命名题库（hard40 等）
tools/               题库生成与校验脚本
docs/                设计说明、任务不变量、题库说明、参考结果
web_server.py + web/ 本地看板
```

## 参考结果

`docs/results/hard40.md`：同一套 40 题上几轮跑的通过数、步数与 token 消耗。各轮裁判不同，跨轮只作趋势参考。

## 仓库里没有什么

- 本地评测记录（`runs/`）、运行日志（`logs/`）与真实的端点配置（`models.yaml`）不入库；公开只有 `models.example.yaml`。
- `liberty-extract-001` 用的是一个**合成** Liberty 库（`assets/liberty/demo28lp_*.lib`，45MB、1039 个 cell、13760 个 timing group，分布按真实库的统计特征对齐），内容全部由 `tools/gen_liberty_fake.py` 程序生成，不含任何厂商数据，`python3 tools/gen_liberty_fake.py` 可原样重建。
- 任何第三方 PDK 库都不入库；`.gitignore` 已按文件名挡掉。
