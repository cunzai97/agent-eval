"""把一道题的真实请求原样发给模型，并把**响应体**打出来。

存在的理由：`provider.py` 走 `raise_for_status()`，异常文本里只有状态码，而
`evaluator.py` 把 `termination == "error"` 直接记成任务失败——"上游返回 400"和
"模型做错了题"在结果里长得一模一样。实测的 data-pipeline-008 就是这种混淆：连跑
两次都在"step 1"报 400，只有拿到响应体才看清是 exceed_context_size_error
(184397 tokens > 99072 n_ctx)：`read_file` 的一次普通读取撑爆了上下文。注意
`agent_loop` 的步数下标从 0 起，所以"step 1"其实是第二次请求。

    python3 tools/probe_request.py --task data-pipeline-008 --turns 3

它按 `agent_loop.run_agent` 的方式组装请求（同样的 tools_schema 过滤、同样的 done
注入、同样的 temperature/seed/max_tokens），并真实执行模型要调的工具再回填观测，
所以第 2 轮之后与实跑一致。这是真实推理请求，会占 GPU：只用来定位，不用来判分。
"""
from __future__ import annotations
import sys, glob, json, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
import yaml
from evalkit.schema import TaskSpec
from evalkit.provider import ModelProvider
from evalkit.envs import get_env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--model", default="qwen3.8-27b-1235")
    ap.add_argument("--turns", type=int, default=2,
                    help="连发几轮（含真实工具回填）；只看第一轮看不到超上下文的 400，默认 2")
    a = ap.parse_args()

    spec = None
    for f in sorted(glob.glob(str(ROOT / "tasks/*/*.yaml"))):
        d = yaml.safe_load(open(f))
        if d["id"] == a.task:
            spec = TaskSpec(**d)
            break
    if spec is None:
        print("no such task", a.task)
        return 1

    cfgs = yaml.safe_load(open(ROOT / "models.yaml"))
    cfgs = cfgs["models"] if isinstance(cfgs, dict) else cfgs
    cfg = next((m for m in cfgs if m.get("name") == a.model), None)
    if cfg is None:
        # 实跑用的名字不在 models.yaml 里，退回到网关三件套 + 与 manifest 一致的参数
        cfg = {"name": a.model, "base_url": "http://127.0.0.1:1235/v1", "model": "local",
               "wire_api": "chat", "temperature": 0.0, "seed": 42,
               "timeout_s": 900, "max_output_tokens": 16384}
    prov = ModelProvider(cfg)

    import tempfile
    tmp = tempfile.mkdtemp(prefix="probe-")
    env = get_env(spec.environment.name, snapshot=spec.environment.snapshot,
                  work_dir=tmp, base="environments")
    env.setup()
    # 与 agent_loop.run_agent 完全一致的 tools_schema（含注入的 done）
    from evalkit.agent_loop import DONE_TOOL
    allowed = set(spec.agent.tools)
    tools_schema = [t for t in env.tools() if t.get("function", {}).get("name") in allowed]
    if "done" in allowed:
        tools_schema.append(DONE_TOOL)
    prov.start(spec.agent.system_prompt, spec.task_prompt, tools_schema)

    for i in range(a.turns):
        body = {
            "model": prov.model,
            "messages": prov._history,
            "temperature": prov.temperature,
            "max_tokens": prov.max_output_tokens,
            "tools": prov._tools,
        }
        if prov.seed is not None:          # 与 ModelProvider._generate_chat 保持一致，
            body["seed"] = prov.seed       # 否则探针会放过只有带 seed 才复现的失败
        r = httpx.post(f"{prov.base_url}/chat/completions", headers=prov._headers,
                       json=body, timeout=prov.timeout)
        print(f"--- turn {i+1}: HTTP {r.status_code}  prompt_chars={sum(len(m['content']) if isinstance(m['content'], str) else 0 for m in prov._history)} tools={len(prov._tools)}")
        if r.status_code != 200:
            print("RESPONSE BODY:\n" + r.text[:4000])
            return 1
        data = r.json()
        print("usage", data.get("usage"))
        msg = data["choices"][0]["message"]
        print("content:", (msg.get("content") or "")[:600])
        print("tool_calls:", [tc["function"]["name"] for tc in (msg.get("tool_calls") or [])])
        prov._history.append({"role": "assistant", "content": msg.get("content") or "",
                              "tool_calls": msg.get("tool_calls") or []})
        for tc in (msg.get("tool_calls") or []):
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            res = env.call_tool(fn["name"], args)
            prov._history.append({"role": "tool", "tool_call_id": tc.get("id"),
                                  "name": fn["name"],
                                  "content": json.dumps(res.to_obs(fn["name"]), ensure_ascii=False, default=str)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
