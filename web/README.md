# Tracebench Web

从项目根目录启动带评测 API 的本地控制台：

```bash
python3 web_server.py --port 4173
```

然后访问 `http://127.0.0.1:4173`。独立实时控制台位于
`http://127.0.0.1:4173/console.html`。不要使用 `python3 -m http.server`，它只能提供静态页面，
无法从 Web 启动或控制评测。

Web Runner 的约束：

- 只监听 `127.0.0.1`；
- 同一时间只运行一个评测 run；
- 单个 run 支持 1–4 个并发任务；
- 模型和任务范围必须来自 `models.yaml` 与 `tasks/`；
- 裁判（LLM-judge）默认开着，用 `models.yaml` 里 `judge.default` 指定的那把尺（现默认
  `mimo-flash`）；发车面板的「LLM 裁判」下拉框可以点名其他档案或选「不启用」，
  缺密钥的档位会直接置灰；
- 连接端点来自 `models.yaml`，但模型结果名称和 API Model ID 可在页面中自行填写；
- 单次响应最大 token 可设为 256–262144，请求超时可设为 10–3600 秒；
- 输出自动写入 `runs/web-<timestamp>/`；
- 完成后自动生成 `report.md` 并刷新 `web/data.js`；
- 实时控制台展示 worker、逐题结果、token、耗时、失败证据与原始进程输出；
- 暂停采用协作式安全点：当前模型请求或工具调用完成后暂停，暂停时间不计入任务超时；
- 暂停后可以继续或停止，停止仍会保留已经完成的任务结果。

页面中的“评测历史”会同时读取已入库历史和 `runs/*/manifest.json`，因此中断的 run 也可见；
“模型排名”只使用 `_HISTORY.jsonl` 中的完整 run，按任务样本加权通过率排序，并同时展示
覆盖样本、平均 token、成功任务平均步数与平均耗时。
