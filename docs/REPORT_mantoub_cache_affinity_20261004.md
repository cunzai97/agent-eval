# mantoub 缓存"只认 codex"之谜：关键原因与解决方案

日期：2026-10-04　　作者：Qoder（受控实验 + 10 天原始日志）
结论一句话：**不是 codex 的缓存不被淘汰，而是 mantoub（New-API 网关）按"会话指纹"做渠道亲和路由。带指纹的请求被钉在同一条上游渠道上，前缀缓存自然留得住；不带指纹的请求在多条渠道间轮询，每打到一条新渠道就等于冷启动。**

---

## 1. 现象（10 天原始日志，7250 次调用）

判据口径：同一会话内相邻两次调用，**上一轮确实命中过缓存（cached>3k）、本轮 prompt 只增不减、≥6k token** —— 即"前缀逻辑上必然可复用"的场合。

| 客户端 | 出口 | 模型 | 相邻对 | 缓存塌方率 | 命中中位 |
|---|---|---|---|---|---|
| codex 0.156.1 | mantoub `/v1/responses` | gpt-5.6-sol | 162 | **0.6%** | 100% |
| codex | mantoub | gpt-6-luna | 37 | **0.0%** | 99% |
| pi | mantoub | gpt-6-luna | 980 | 14.4% | 80% |
| pi | mantoub | gpt-6-sol | 635 | 17.8% | 93% |
| pi | mantoub | gpt-6.1-sol | 238 | 34.9% | 64% |
| evalkit（本项目裸 httpx） | mantoub | gpt-5.6/6-luna | 59 | **67.8%** | 68-83% |
| pi | 本地 llama.cpp :1235 | local | 2566 | 1.2% | 101% |
| pi | 本地 :1236 | local2 | 462 | 0.6% | 101% |
| pi | api.deepseek.com | deepseek-flash | 789 | 0.0% | 100% |

同一把 key、同一个 `/v1/responses`、同一批模型、同一个代理出口，codex 0%、其他 14-68%。

## 2. 被排除的解释

| 假说 | 判据 | 结论 |
|---|---|---|
| "pi 自己往 prompt 前部注入动态内容，打碎前缀"（我 10-04 19:25 的说法） | pi 在本地/ deepseek 上塌方 0-1%；pi 的 system 变更只占 370 次塌方中的 5 次 | **否** |
| 两把不同的 API key / 不同分组 | pi 的 `models.json` 里 mantoub `apiKey: $MANTOUB_API_KEY`，与 codex 同一个值 | **否** |
| 出口 IP 不同（pi 走 `127.0.0.1:18443` 前置） | 前置只是经 mihomo 的透明转发器，不改请求；且 mantoub 直连被 RST，两边同走一个代理出口，出口 IP 三次采样不变 | **否** |
| 网关负载/时段 | 同一小时内对照：12 时 pi 8/33=24% vs codex 0/100=0%；8 时 pi 2/3 vs codex 1/32=3% | **否** |
| 请求规模 | 分桶后 pi 在 15-40k 仍 35%、>90k 仍 18%；codex 各桶 0-2% | **否** |
| 跨客户端同时驱逐（互相挤） | 311 个有塌方的分钟里，≥2 种客户端同一分钟同时塌：**0 个** | **否** |

## 3. 决定性实验：抓真实 codex 信封，逐字段剥离回放

用 `codex exec` 指向本地一次性记录端口（不碰正在运行的桥与客户端），抓到真实请求：
`store:false`、`include:["reasoning.encrypted_content"]`、`prompt_cache_key=<session UUID>`、
以及 `session-id` / `thread-id` / `x-client-request-id` / `x-codex-window-id` / `originator` / `user-agent: codex_cli_rs/0.156.1`。

回放时**内容完全不变**（同一份 12,485 token 前缀），只改信封与指纹，每格预热 1 次 + 复用 3 次：

| 变体 | 相对 codex 原样的差异 | 复用命中 |
|---|---|---|
| V0 | 原样 | **8/8**（三轮独立复现） |
| V2 | 只删 `prompt_cache_key`，其余全留 | 1/3 |
| V1 | 删全部 codex 头，body 与 key 留 | 1/2 |
| V5 | 删大部分头，留 `session-id`/`thread-id` + key | 2/2 |
| V4 | 最小信封（无 key、无 codex 头） | 0/3 |
| W1 | 最小信封 + 只补 `prompt_cache_key`（UUID 形） | 1/6 |
| **X1** | **最小信封 + 完整会话指纹**（key 与 `session-id`/`thread-id`/`x-client-request-id` 同一个 UUID） | **6/6，命中率 94%** |
| X2 | 同 X1，但每次调用换一个 UUID | **0/3** |
| W1 | 最小信封 + 只有 `prompt_cache_key`（不带任何 codex 头） | 1/6 |
| Y1 | 最小信封 + 只有那三个头，body 不加 key | **0/3** |
| Y3 | 最小信封 + 只给 `session-id` 一个头 | 0/3 |

X1 与 X2 只差"指纹稳不稳定"，6/6 对 0/3。Y1 与 X1 只差"body 里那行 key"，0/3 对 6/6。
**因果闭环：粘性需要 `prompt_cache_key`（body）与会话头同时在场，缺一即退化成轮询。与客户端是谁、叫什么名字无关。**

## 4. 机制

New-API（响应头 `x-new-api-version: v1.0.0-rc.35`）在同一个模型下挂多条上游渠道。带会话指纹的请求命中亲和路由，始终落到同一条渠道 → 该渠道自己那份前缀缓存被连续复用；不带指纹的请求按轮询/权重分散到多条渠道 → 每次换渠道都要重新 prefill，表现为"缓存忽 0 忽 4864 忽 12800"。
网关的识别条件看起来是**两件事同时成立**：请求被认成"codex 类流量"（靠 `session-id`/`thread-id`/`x-client-request-id` 这组头），并且 body 里带着 `prompt_cache_key` 作为亲和键 —— 只给头（Y1 0/3）或只给键（W1 1/6）都拿不到粘性。
所以群里那句话应当改成：**"不是 codex 的缓存不被淘汰，是 codex 带了会话指纹被钉在同一条渠道上；不带指纹的流量在多渠道间轮询，每换一次渠道就是冷启动。"**

### 4.1 pi 到底缺哪一半（2026-10-05 落地时查到的）

读 pi 源码：`packages/ai/src/api/openai-responses.ts:334` 已经在发 `prompt_cache_key`（取 `options.sessionId`），
但同一份代码 `:277-284` 只发 `x-client-request-id`（`x-session-id` 仅在 openrouter 兼容格式下、`session_id` 仅在 openai 兼容格式下），
**从来不发 `session-id` / `thread-id`**。也就是说 pi 正站在实验里 W1 那一格（只给 body key：1/6）。
这与观测到的 14-35% 塌方率吻合：key 提供一部分粘性，缺的那两个头把它削成了"偶尔换渠道"。
这也决定了前置该做什么 —— 不是"没 key 才补"，而是**把缺的那一半补齐**（见 §5）。
落地时前置的日志直接证实了这一点：桥（TLive → pi）的每条请求都被记成 `affinity <同前 8 位> +session-id,thread-id`，
即"客户端自带 key、缺这两个头、而且会话号在多轮之间是稳的"。

## 5. 解决方案：在源头注入，不给每个 agent 打补丁

现状拓扑（已核实）：

| 客户端 | 指向 | 自带指纹？ |
|---|---|---|
| pi | `http://127.0.0.1:18443/v1` → `mantoub-front.service` | 否 |
| Hermes | `http://127.0.0.1:18443/v1`（`~/.hermes/auth.json` 里 `custom:mantoub`） | 否 |
| codex | `https://mantoub.com/v1` 直连 | **是**（所以它一直命中） |
| evalkit | `https://mantoub.com/v1` 直连（裸 httpx） | 否 |

pi 与 Hermes 已经共用 18443 这个前置，而它已经是 `systemd --user` 服务（`mantoub-front.service`，已稳定跑 9 天）。
**所以最优解是：让前置成为唯一的注入点。** 一处改完，pi、Hermes、以后任何指着 18443 的工具全部受益，不需要碰它们的代码（Hermes 尤其不可控）。

### 前置实际做的三件事（已落地）

1. **取指纹，按"谁先给了就用谁的"**：
   `body.prompt_cache_key` → `session-id` → `x-session-id` → `session_id` 头，依次找第一个现成的值；
   一个都没有时按内容派生 `uuid5(NS, model + "\0" + 第一条 input 文本[:4096])`。
   派生而不是"按 TCP 连接分配"，因为客户端开几条连接、重连、换进程都不影响结果，
   同一会话天然同键、不同会话天然分键（避免所有会话挤在同一条渠道上互相干扰）。
   命名空间 `MF_AFFINITY_NS` 写死在代码里，重启不会给在线会话重新发号。
2. **缺哪半补哪半，已有的一个字节都不改**：body 没有 key 才写 key（写了才重序列化并重算
   Content-Length）；`session-id`/`thread-id`/`x-client-request-id` 三个头只在缺失时补
   （`thread-id` 用 `<fp>:0`，与 codex 同形）。于是 pi 补两个头、裸 httpx 补四个、
   真 codex 走前置时原样透传。
3. **失败即回退**：不是 POST `/v1/responses`、body 为空、content-type 不含 json、
   body 不是对象、既没 key 也没可读文本、解析炸了 —— 一律原样转发，只记一行原因。
   前置本来就把 `content-length` 放在 `DROPPED_REQUEST_HEADERS` 里重新分帧，重算那步是现成的。

日志一行一次、只打指纹前 8 位与补了什么，不打任何内容：`affinity 01a10646 model=gpt-6-luna +session-id,thread-id`。
`MF_AFFINITY=0` 一键关掉（改 unit 文件里加一行 Environment 再重启）。

### 剩下两处收口

- **evalkit**：`models.yaml` 里 luna-max / luna-6（裁判）的 `base_url` 改成
  `http://127.0.0.1:18443/v1`，并新增一行 `upstream_url: https://mantoub.com/v1`。
  为什么要多这一行：端点性质（榜单的 local/external 分层、`--jobs` 上限 4 还是 16）原本照
  `base_url` 的主机名判，指向回环前置就会被标成"本地量化"、并发被压回 4。现在
  `cli._provenance_url()` / `web_server.classify_target()` 都改按真实上游判，
  `web_server.profile_for_runtime()` 让接入前后的历史 run 都还能续跑。
  注意口径：改完外部 API 行的 cached 会大幅上升、prefill 下降，**efficiency/成本类分数会整体变好**，
  与历史行不可直接混比；历史行不重跑、只标注。计费口径**不要**因此改回 `input - cached`
  （`evalkit/agent_loop.py:39-43` 那条原则继续成立）。
- **codex**：不用动。它本来就自带指纹；若为了统一观测把它也指到前置，指纹会被原样透传。

### 复测工具（已就位）

`.scratch/mantoub_envelope_bisect.py`（逐字段剥离回放，含 `min_fp` / `hdr_only` / `min_rotkey` 三种对照）、
`.scratch/mantoub_cache_iso.py`、`.scratch/mantoub_cache_interleave.py`、`.scratch/envelope_recorder.py`（抓信封）、
`.scratch/cache_eviction_probe.py`（从历史日志算塌方率）。前置接入后的实际复测见 §7。

## 6. 未证与风险

- ~~小前缀下收益待测~~：接入后测过 3.1k 前缀的两跳（evalkit 真实 provider），第二跳 cached 2816/3175 = **89%**，
  不是只有 12.5k 才有效。12.5k 那些信封实验只是照抄了 codex 的真实请求大小。
- "网关靠这组头识别 codex 类流量"是从行为反推的：没有读 New-API 的路由实现，也不排除 mantoub 给这类流量单开了一条低负载渠道池。两种解释的**修复动作完全相同**，只是对"能不能长期依赖"的判断不同 —— 若哪天网关改了识别条件，前置注入会静默失效，所以要把塌方率做成可观测项而不是配完就走。
  观测手段：`journalctl --user -u mantoub-front.service | grep affinity` 看每条会话补了什么、指纹稳不稳；
  塌方率用 `.scratch/cache_eviction_probe.py` 从历史日志重算。
- 前置成为关键路径：pi、Hermes、evalkit 都会挂在 `mantoub-front.service` 上，而且它现在会**改 body**。
  兜底都做在里面：只动 POST `/v1/responses`、content-type 不含 json 或解析炸了就原样转发、
  `MF_AFFINITY=0` 可整段关掉、指纹派生失败不影响请求。前置 502 时 evalkit 按上游故障重试
  （`RETRYABLE_STATUS` 含 502），题会记"未评"而不是 FAIL。
- 同一 UUID 跨并发会话复用会不会互相挤占亲和槽，未测。内容派生（每条会话各自一个键）天然规避了"全局共用一个键"的风险。
- 榜单口径断层：接入前置之前/之后的外部 API 行，efficiency/成本类分数不同尺（历史行不重跑）。
  `web_server.py` 的改动要**重启看板进程**才生效（那个常驻进程还带着旧的分层逻辑）；
  在那之前，面板会把 luna 两条算成 local（并发上限 4）、续跑历史 run 时报"找不到匹配的连接配置"。
- 本次实验累计约 90 次外部调用、约 80 万输入 token，按日志中 mantoub 单价约 $3-4；
  接入验收另加 9 次（三臂信封）+ 2 次（evalkit 两跳）。

## 7. 落地清单与验收（2026-10-05 00:20-00:35）

| 改了什么 | 位置 |
|---|---|
| 指纹补全（body key + 三个会话头，缺哪半补哪半）、`MF_AFFINITY`/`MF_AFFINITY_NS`/`MF_AFFINITY_TEXT_LIMIT`、跳过原因日志 | `~/.config/proxy-bridge/mantoub-front.py`，已重启生效 |
| luna-max / luna-6 / judge 指到前置 + 声明 `upstream_url` | `models.yaml` |
| 端点性质按真实上游判：`_provenance_url()`、manifest 记 `upstream_url`、面板临时改地址时作废 | `cli.py` |
| `classify_target()` / `profile_for_runtime()`，catalog 带 `upstreamUrl`，发车与续跑的天花板都按真实上游算 | `web_server.py` |
| 新断言：转发器后面的云端端点仍是 external/16、历史 run 仍能续跑、临时改地址按新地址算 | `tools/test_board_api.py`、`tools/test_infra_retry.py`（39/39 通过） |
| 离线信封形状测试（裸请求 / pi 形 / codex 形 / 只有头 / 非 json / 坏 json / 追加轮次 / 换模型 / 开关） | `.scratch/mantoub_front_unit.py` |
| 在线验收（A/B/D 三臂）与 evalkit 两跳冒烟 | `.scratch/mantoub_front_accept.py`、`.scratch/evalkit_front_smoke.py` |

验收数字（全部走 18443，每次调用新建 TCP 连接，所以命中只能来自渠道亲和；前缀 12,488 token）：

| 臂 | 客户端给了什么 | 前置补了什么 | 结果 |
|---|---|---|---|
| A 裸请求（evalkit / Hermes 的形状） | 无 key 无会话头 | key + 三个头 | **2/2 命中，94%** |
| B 自带轮换 key | 每次一个新 UUID | 只补头 | **0/2，0%**（对照：稳不稳才是关键） |
| D pi 形状 | 稳定 key + `x-session-id`/`x-client-request-id` | `session-id` + `thread-id` | **2/2 命中，94%** |
| evalkit 真实两跳 | 3.1k 前缀 | key + 三个头 | 第二跳 cached 2816/3175 = **89%** |

`GET /v1/models` 仍 200；重启前后无 5xx、无 replay；桥（TLive→pi）的指纹在多轮之间保持同一个
`01a10646…`，说明它的会话号本来就稳，缺的只是那两个头。

还差最后一步观测：让 pi / Hermes / 桥各跑一段真实多轮会话，再用 `.scratch/cache_eviction_probe.py`
从日志重算 mantoub 行的塌方率，看 14-35% 是否掉到个位数。
