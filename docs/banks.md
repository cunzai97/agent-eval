# 题库(bank)选择

开跑前从命名题库里选题，原 tasks/ 全部保持原样，题库只是一份 id 清单。

## 用法

```bash
python3 cli.py banks                        # 列出可用题库
python3 cli.py run --bank hard40 ...        # 用题库选题开跑
python3 cli.py run --bank hard40 --tasks locate ...   # 与过滤取交集（只跑题库里的 locate）
```

## 现有题库

- `hard40`：62 题全库中按**历史整体通过率升序**取前 40（runs/_HISTORY.jsonl，85 次 run）。
  历史零失败（从没难倒过任何模型）的题被排除；新开发未测难题全部包含。
  切线题 debug-stacktrace-009 = 88%。每题历史通过率记录在 banks/hard40.yaml 的 detail: 段。
- `horizon`：5 道新增中长链路编程题 + 12 道历史中等耗时、有区分度的题，共 17 题；全部确定性判分、无 rubric。
  旧题口径：同一模型同一题最近一次 graded 尝试，n≥20、通过率 40%–89%、中位耗时 ≤300s（实测 42.6%–88.9%、46–286s）。
  剔除 hard40 中 3 道最长题（477s/378s/342s）、4 道高耗时低区分度题、2 道零区分度题、3 道通过率 <12% 的墙题及 7 道裁判依赖题。
  新题有参考解和变异负控，但模型实跑耗时与通过率仍待建基线；逐题口径见 banks/horizon.yaml 与 docs/horizon.md。
- `horizon38`：horizon 的"难而不慢"扩容版，共 38 题。选法：无 rubric、n≥20、中位耗时 ≤300s 的快题池
  共 39 道，按历史通过率**从低到高**取 35（保留 5%/298s、11%/265s 两道墙题）；再加 10-28 新题里
  首轮 ≤300s 的 3 道。慢题无论多难、多新一律剔除：历史慢题 8 道（520s/453s/421s/902s 等），
  新题 2 道超时（416.6s、340.7s）。切掉的 4 道快题是池里最易的（96%–98%）。38 题历史中位合计约 1.36h
  （jobs=1）。池子只有 39+3，要凑满 40 需新开发 2 道难且快的题。逐题口径见 banks/horizon38.yaml。

## 再生成

题库定义 `banks/<name>.yaml` 由脚本生成、可重跑（历史数据会增长，建议比赛前重新生成一次）：

```bash
python3 tools/build_bank.py --n 40 --name hard40
```

改动 tasks/ 或新增题库后无需注册：`--bank` 直接读 banks/ 目录。

## 总分排名

```bash
python3 tools/bank_board.py --bank hard40 --model luna-6 --alias luna6-new12=luna-6
```

- 读 runs/_HISTORY.jsonl，同题后跑覆盖先跑；`--alias` 把某次 run 的标签并进目标模型（不用改历史账本）。
- 默认只显示跑过 ≥20 题的模型（滤掉 smoke/demo 单题标签），`--min-ran` 可调。
- 输出两个口径：**全库榜**（只统计各模型跑过的题，题数不同不完全可比）与**共同子集榜**（所有模型都跑过的题，公平对比）。
