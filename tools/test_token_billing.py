#!/usr/bin/env python3
"""预算计费的不变量：只有被试自己的行为能移动 max_tokens 这条线。

2026-09-22 曾把计费从"相对上一步 prompt 的增量"改成"input - 上游报的 cached_tokens"，
第二天就在 某上游的 Responses 口上翻车：同一个会话里 cached 忽报 0、忽报 6656、忽报
12800，于是同一条轨迹的计费从 23,620 涨到 69,079，一行 50 题里 6 题（5.6 那行 10 题）
被 64,000 的预算线切掉。分数动了，被试什么都没变——这是归因事故，不是口径改良。

所以这里钉死：``cached`` 只进 ``Step.tokens`` 当观测数据，**绝不参与判定**。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evalkit.agent_loop import _new_input_tokens, run_agent  # noqa: E402
from evalkit.provider import _cached_tokens  # noqa: E402
from evalkit.schema import (AgentSpec, Budget, EnvironmentSpec, SuccessCheck,  # noqa: E402
                            TaskSpec, ToolCall, Usage)

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}: got {got!r} want {want!r}")


def usage(inp, out, cached="__unset__"):
    return Usage(input=inp, output=out, cached=None if cached == "__unset__" else cached)


# ---------------------- 单元：口径与解析 ----------------------
def unit_cases():
    print("=== 口径：增量计费，cached 不动它 ===")
    base = usage(1200, 30)
    hit = usage(1200, 30, cached=1200)        # 上游说整段都命中
    miss = usage(1200, 30, cached=0)          # 上游说一点没命中
    bogus = usage(1200, 30, cached=999)       # 上游乱报一个中间值
    for label, u in (("未报", base), ("全命中", hit), ("零命中", miss), ("乱报", bogus)):
        check(f"{label}口径下都按增量计", _new_input_tokens(u, 1000), 200)
    check("prompt 变短（上下文压缩）不能变免费步", _new_input_tokens(usage(800, 30), 1000), 0)

    print("=== 解析：能分清『没报字段』和『报了 0』 ===")
    check("chat 口取 prompt_tokens_details",
          _cached_tokens({"prompt_tokens_details": {"cached_tokens": 6656}},
                         "prompt_tokens_details"), 6656)
    check("responses 口取 input_tokens_details",
          _cached_tokens({"input_tokens_details": {"cached_tokens": 12800}},
                         "input_tokens_details", "prompt_tokens_details"), 12800)
    check("字段整个缺失 -> None（不是 0）", _cached_tokens({}, "prompt_tokens_details"), None)
    check("显式报 0 -> 0", _cached_tokens({"prompt_tokens_details": {"cached_tokens": 0}},
                                         "prompt_tokens_details"), 0)


# ---------------------- 集成：跑完整循环 ----------------------
class ScriptedProvider:
    name = "scripted"
    seed = 0

    def __init__(self, responses):
        self.responses = list(responses)
        self.retry_stats = {"attempts": 0, "recovered": 0, "gave_up": 0}

    def start(self, system, task, tools):
        pass

    def generate(self, timeout_s=None):
        return self.responses.pop(0)

    def submit_tool_results(self, results):
        pass

    def request_continuation(self):
        pass


class Resp:
    def __init__(self, u, calls=None):
        self.usage = u
        self.tool_calls = calls or []
        self.content = ""


class QuietEnv:
    """带一个无害工具：让每步都带工具调用，不去撞 stalled 口径。"""

    def tools(self):
        return [{"type": "function", "function": {"name": "peek", "parameters": {}}}]

    def call_tool(self, name, args):
        from evalkit.envs.base import ToolResult
        return ToolResult(ok=True, result={"n": 1})

    def state(self):
        return {}

    def teardown(self):
        pass


def task_with(max_tokens):
    return TaskSpec(id="t-billing", title="t", category="debug",
                    environment=EnvironmentSpec(name="code_repo", snapshot="none"),
                    agent=AgentSpec(system_prompt="s", tools=["done", "peek"]),
                    task_prompt="p",
                    budget=Budget(max_steps=10, max_tokens=max_tokens, timeout_s=60),
                    success=[SuccessCheck(type="tool_used", name="done")])


def call(i):
    return [ToolCall(id=f"c{i}", name="peek", args={})]


def done(i):
    return [ToolCall(id=f"c{i}", name="done", args={})]


def growth(cached_mode):
    """同一条 prompt 递增轨迹，只有"上游怎么报缓存"不同。"""
    seq = []
    for k, inp in enumerate((10_000, 11_000, 12_000, 13_000)):
        cached = {"hit": inp - 1000, "miss": 0, "silent": "__unset__"}[cached_mode]
        seq.append(Resp(usage(inp, 100, cached), done(k) if k == 3 else call(k)))
    return seq


def integration_cases():
    print("=== run_agent：三种上游报法必须给出同一笔账 ===")
    out = {}
    for mode in ("hit", "miss", "silent"):
        t = run_agent(task_with(64000), ScriptedProvider(growth(mode)), QuietEnv())
        out[mode] = (t.total_tokens, t.termination, len(t.steps))
    check("全命中 == 零命中", out["hit"], out["miss"])
    check("全命中 == 未报字段", out["hit"], out["silent"])
    # 增量口径：首步没有"上一步 prompt"可比，按整段计（10000+100）；其后每步只算新增。
    check("账目=首步整段 + 其后逐段增量", out["hit"], (13400, "done", 4))

    print("=== 事故回放：上游一口咬定零命中，也不该把题切死 ===")
    # 2026-09-22 那个错误口径在这种报法下会逐段全额重计：
    # (10000+11000+12000+13000) + 4×100 = 46,400，是真实账 13,400 的 3.5 倍。
    # 这里把这个数字钉住：谁要是再让 cached 进判定，第一条断言就会裂。
    check("零命中报法下预算线仍只看增量", out["miss"], (13400, "done", 4))
    check("错误口径会多记的倍数仍然成立",
          sum(u for u in (10_000, 11_000, 12_000, 13_000)) + 400, 46400)
    tight = run_agent(task_with(20000), ScriptedProvider(growth("miss")), QuietEnv())
    check("20k 预算下这条轨迹该跑完而不是撞死",
          (tight.total_tokens, tight.termination, len(tight.steps)), (13400, "done", 4))

    print("=== 观测仍然留痕 ===")
    t2 = run_agent(task_with(9999), ScriptedProvider(
        [Resp(usage(500, 40, cached=400), done(0))]), QuietEnv())
    check("Step.tokens 里带着 cached", t2.steps[0].tokens.cached, 400)
    check("但它不进账", t2.total_tokens, 500 + 40)


if __name__ == "__main__":
    unit_cases()
    integration_cases()
    print(f"\n通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
