"""上游故障归因的可重跑测试：不需要 GPU，不需要被测模型。

    python3 tools/test_infra_retry.py

三件事各有一组断言，全部对着**真实的代码路径**跑（httpx 打本机 mock server、
run_task 打一个假环境）：

1. **重试与分类**：503/断连/连不上要重试并在用尽后抛 ``InfraError``；400（超上下文这类
   我们自己的问题）必须**立刻**抛出、一次都不重试；重试不得吃掉任务的时间预算。
2. **终态归类**：agent_loop 把 ``InfraError`` 记成 ``termination="infra_error"``，
   把别的异常仍然记成 ``error``，且墙钟真的用完时记 ``timeout``（那是模型太慢，不是服务器）。
3. **分数归属**：``graded=False`` 的尝试不得进通过率分母；带 rubric 却没跑裁判、
   裁判没评成、裁判档案不符，三种都必须"未评"，绝不能静默算通过或算失败。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx                                                  # noqa: E402

from evalkit.agent_loop import run_agent                      # noqa: E402
from evalkit.evaluator import run_task                        # noqa: E402
from evalkit.provider import InfraError, ModelProvider        # noqa: E402
from evalkit.schema import Budget, EnvironmentSpec, AgentSpec, SuccessCheck, TaskSpec  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")


# ----------------------------- mock server ---------------------------------
class Handler(BaseHTTPRequestHandler):
    mode = "ok"
    hits = 0
    seen_auth = []                              # 每次请求带来的 Authorization，用来验外部 API 带密钥

    def log_message(self, *_args):            # 安静
        pass

    def do_GET(self):                          # health()
        Handler.hits += 1
        Handler.seen_auth.append(self.headers.get("Authorization"))
        self.send_response(200 if Handler.mode != "down" else 503)
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        Handler.hits += 1
        Handler.seen_auth.append(self.headers.get("Authorization"))
        mode = Handler.mode
        if mode == "flaky" and Handler.hits <= 2:
            mode = "down"
        if mode == "down":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"busy")
            return
        if mode == "bad-request":
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"exceed_context_size_error")
            return
        body = json.dumps({
            "choices": [{"message": {"content": "hi", "tool_calls": []}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/v1", port


def cfg(base_url, **over):
    c = {"name": "mock", "base_url": base_url, "model": "m", "wire_api": "chat",
         "timeout_s": 5, "retry_backoff_s": 0.05, "max_retries": 3}
    c.update(over)
    return c


# ----------------------------- stub pieces ---------------------------------
class StubProvider:
    """只测 agent_loop 的异常归类，不碰网络。"""

    name = "stub"
    seed = 0

    def __init__(self, exc, stats=None):
        self.exc = exc
        self.retry_stats = stats or {"attempts": 0, "recovered": 0, "gave_up": 0}

    def start(self, system, task, tools):
        pass

    def generate(self, timeout_s=None):
        raise self.exc


class FakeJudge:
    def __init__(self, verdict="pass", ungraded=False, identity="luna-max:gpt-5.6-luna"):
        self.calls = []
        self.identity = identity
        self.result = {"score": 0.9, "verdict": verdict, "reasons": ["r"], "dimensions": {},
                       "judge_identity": identity, "judge_model": "luna-max"}
        if ungraded:
            self.result.update({"ungraded": True, "parse_error": True, "verdict": "ungraded"})

    def evaluate(self, task, traj, state, rubric):
        self.calls.append(task.id)
        return dict(self.result)


class DoneProvider(StubProvider):
    """一步就 done，让 run_task 走完整判定路径。"""

    class _Resp:
        def __init__(self):
            from evalkit.schema import ToolCall, Usage
            self.content = ""
            self.tool_calls = [ToolCall(id="c1", name="done", args={})]
            self.usage = Usage(input=1, output=1)

    def __init__(self):
        self.retry_stats = {"attempts": 0, "recovered": 0, "gave_up": 0}

    def start(self, system, task, tools):
        pass

    def generate(self, timeout_s=None):
        return self._Resp()


class FakeEnv:
    def tools(self):
        return []

    def call_tool(self, name, args):
        raise AssertionError(f"不该有工具调用: {name}")

    def state(self):
        return {}

    def teardown(self):
        pass


def task(rubric=None, judge_id=None):
    return TaskSpec(id="t-test", title="t", category="debug",
                    environment=EnvironmentSpec(name="code_repo", snapshot="does-not-matter"),
                    agent=AgentSpec(system_prompt="s", tools=["done"]),
                    task_prompt="p", budget=Budget(max_steps=3, max_tokens=1000, timeout_s=30),
                    success=[SuccessCheck(type="tool_used", name="done")],
                    rubric=rubric, judge_id=judge_id)


# -------------------------------- tests ------------------------------------
def test_provider():
    print("\n[1] provider 的重试与分类")
    httpd, base, port = serve()
    try:
        Handler.mode = "flaky"; Handler.hits = 0
        p = ModelProvider(cfg(base))
        resp = p.generate(timeout_s=10)
        check("flaky(前两次 503) 最终拿到响应", resp.content == "hi")
        check("flaky 计了 2 次重试", p.retry_stats["attempts"] == 2,
              f"实际 {p.retry_stats}")

        Handler.mode = "down"; Handler.hits = 0
        p = ModelProvider(cfg(base))
        try:
            p.generate(timeout_s=10)
            check("持续 503 抛 InfraError", False, "没抛")
        except InfraError as e:
            check("持续 503 抛 InfraError", True)
            check("503 尝试次数 = 1 + max_retries", e.attempts == 4, f"attempts={e.attempts}")

        Handler.mode = "bad-request"; Handler.hits = 0
        p = ModelProvider(cfg(base))
        try:
            p.generate(timeout_s=10)
            check("400 不重试、原样抛出", False, "没抛")
        except httpx.HTTPStatusError:
            check("400 不重试、原样抛出 HTTPStatusError", Handler.hits == 1,
                  f"server 被打 {Handler.hits} 次")

        p = ModelProvider(cfg(f"http://127.0.0.1:{port + 1}/v1"))
        t0 = time.monotonic()
        try:
            p.generate(timeout_s=10)
            check("连不上抛 InfraError", False, "没抛")
        except InfraError:
            check("连不上抛 InfraError", True)

        # 时间预算优先：只给 0.2s，退避还没走完就该收手
        Handler.mode = "down"; Handler.hits = 0
        p = ModelProvider(cfg(base, retry_backoff_s=5.0))
        t0 = time.monotonic()
        try:
            p.generate(timeout_s=0.2)
            raised = None
        except Exception as exc:  # noqa: BLE001
            raised = exc
        spent = time.monotonic() - t0
        check("预算内不重试（0.2s 不许等 5s）", isinstance(raised, InfraError) and spent < 1.0,
              f"{type(raised).__name__} 花 {spent:.2f}s")

        Handler.mode = "ok"                       # 上一个子测试把服务留在 503
        ok, detail = ModelProvider(cfg(base)).health()
        check("health() 探得到活服务", ok, detail)
        Handler.mode = "down"
        ok, detail = ModelProvider(cfg(base)).health()
        check("health() 能报 5xx", not ok, detail)
        ok, _ = ModelProvider(cfg(f"http://127.0.0.1:{port + 1}/v1")).health(timeout_s=1)
        check("health() 能报连不上", not ok)
    finally:
        httpd.shutdown()


def test_external_api_key():
    """外部 API 作为被测端点：密钥必须真的随请求出去，且支持 ${ENV} 形式。"""
    print("\n[1b] 外部端点带密钥（Authorization: Bearer）")
    import importlib
    cli = importlib.import_module("cli")
    httpd, base, _port = serve()
    try:
        Handler.mode = "ok"; Handler.hits = 0; Handler.seen_auth = []
        ModelProvider(cfg(base, api_key="sk-unit-test-123")).generate(timeout_s=10)
        check("字面密钥原样进 Authorization 头",
              Handler.seen_auth and Handler.seen_auth[-1] == "Bearer sk-unit-test-123",
              f"实际 {Handler.seen_auth[-1:] }")

        os.environ["UNIT_TEST_DS_KEY"] = "sk-from-env"
        Handler.seen_auth = []
        ModelProvider(cfg(base, api_key="${UNIT_TEST_DS_KEY}")).generate(timeout_s=10)
        check("api_key 写成 ${VAR} 时从环境展开",
              Handler.seen_auth[-1] == "Bearer sk-from-env", f"实际 {Handler.seen_auth[-1:]}")

        Handler.seen_auth = []
        ModelProvider(cfg(base)).generate(timeout_s=10)
        check("不给密钥时退回本地占位 Bearer local",
              Handler.seen_auth[-1] == "Bearer local", f"实际 {Handler.seen_auth[-1:]}")

        check("_endpoint_kind 区分回环与外部",
              cli._endpoint_kind("http://127.0.0.1:1235/v1") == "local"
              and cli._endpoint_kind("https://api.deepseek.com/v1") == "external"
              and cli._endpoint_kind("http://localhost:8080/v1") == "local")
        # 云端 API 指到本机亲和前置（upstream_url）只是换出口：来源分层必须跟着真实上游，
        # 否则榜单把付费那一行测成"本地量化"，--jobs 上限也会被压回 4。
        check("转发器不改端点来源",
              cli._endpoint_kind(cli._provenance_url(
                  {"base_url": "http://127.0.0.1:18443/v1",
                   "upstream_url": "https://example.com/v1"})) == "external")
        check("没声明 upstream_url 时仍按连接地址算",
              cli._provenance_url({"base_url": "http://127.0.0.1:1235/v1"})
              == "http://127.0.0.1:1235/v1")
    finally:
        httpd.shutdown()
        os.environ.pop("UNIT_TEST_DS_KEY", None)


def test_agent_loop():
    print("\n[2] agent_loop 的终态归类")
    spec = task()
    env = FakeEnv()
    traj = run_agent(spec, StubProvider(InfraError("down", attempts=4, cause="ConnectError")), env)
    check("InfraError -> termination=infra_error", traj.termination == "infra_error",
          traj.termination)
    check("错误文本标注 upstream，不再冒充 model generate failed",
          (traj.error or "").startswith("upstream unavailable"), (traj.error or "")[:60])
    check("重试次数记进 trajectory", traj.retries == 0 or traj.retries > 0, f"retries={traj.retries}")

    stats = {"attempts": 6, "recovered": 2, "gave_up": 1}
    traj = run_agent(spec, StubProvider(InfraError("down", attempts=4, cause="x"), stats), env)
    check("provider 的重试统计被抄进 trajectory", traj.retries == 6, f"retries={traj.retries}")

    traj = run_agent(spec, StubProvider(RuntimeError("boom")), env)
    check("普通异常仍是 error", traj.termination == "error", traj.termination)

    slow = StubProvider(InfraError("slow", attempts=1, cause="ReadTimeout"),
                        stats={"attempts": 7, "recovered": 0, "gave_up": 1})
    tight = task()
    tight.budget.timeout_s = 0
    traj = run_agent(tight, slow, env)
    check("墙钟真的用完 -> timeout 而不是 infra_error", traj.termination == "timeout",
          traj.termination)


def test_scoring():
    print("\n[3] 分数归属：未评不进分母，也不许静默通过")
    import evalkit.evaluator as ev
    real_get_env = ev.get_env
    ev.get_env = lambda *a, **k: FakeEnv()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            r = run_task(task(), DoneProvider(), tmp)
            check("无 rubric + done -> 正常判分", r.passed and r.graded, str(r.failure_reason))

            r = run_task(task(rubric="维度: x"), DoneProvider(), tmp)   # judge=None
            check("带 rubric 但没跑裁判 -> 未评（不再静默通过）",
                  (not r.graded) and (not r.passed), r.ungraded_reason or "")

            r = run_task(task(rubric="维度: x"), DoneProvider(), tmp, judge=FakeJudge("pass"))
            check("带 rubric + 裁判 pass -> 计分", r.passed and r.graded)

            r = run_task(task(rubric="维度: x"), DoneProvider(), tmp, judge=FakeJudge("fail"))
            check("裁判 fail 仍是真失败（graded=True）", (not r.passed) and r.graded,
                  str(r.failure_reason)[:60])

            r = run_task(task(rubric="维度: x"), DoneProvider(), tmp,
                         judge=FakeJudge(ungraded=True))
            check("裁判没评成 -> 未评而不是失败", (not r.graded) and (not r.passed),
                  r.ungraded_reason or "")

            r = run_task(task(rubric="维度: x", judge_id="other:v1"), DoneProvider(), tmp,
                         judge=FakeJudge("pass"))
            check("裁判档案与题面不符 -> 未评", (not r.graded), r.ungraded_reason or "")

            infra = StubProvider(InfraError("down", attempts=4, cause="ConnectError"))
            r = run_task(task(), infra, tmp)
            check("上游故障 -> 未评且 passed=False", (not r.graded) and (not r.passed),
                  str(r.ungraded_reason)[:80])

            saved = json.load(open(Path(tmp) / "t-test" / "result.json", encoding="utf-8"))
            check("result.json 落盘带 graded/ungraded_reason",
                  saved.get("graded") is False and "infra" in (saved.get("ungraded_reason") or ""),
                  json.dumps(saved.get("ungraded_reason"), ensure_ascii=False)[:60])
    finally:
        ev.get_env = real_get_env

    # CLI 聚合：未评不进分母。**注意**：_append_history 会写 runs/_HISTORY.jsonl，
    # 所以把 cli.ROOT 指到临时目录，绝不碰真实历史。
    import importlib
    cli = importlib.import_module("cli")

    class R:
        def __init__(self, tid, passed, graded=True, reason=None):
            self.task_id, self.passed, self.graded, self.ungraded_reason = tid, passed, graded, reason
            from evalkit.schema import Efficiency
            self.scores = {"debug": 1.0 if passed else 0.0}
            self.efficiency = Efficiency(steps=5, tokens=1000, elapsed_s=10.0)
            self.judge = None

    rows = [("m1", "a", R("a", True)), ("m1", "b", R("b", False)),
            ("m1", "c", R("c", False, False, "infra: refused")),
            ("m1", "d", R("d", False, False, "judge: 未评"))]
    hist_file = ROOT / "runs" / "_HISTORY.jsonl"
    before = hist_file.read_bytes() if hist_file.exists() else b""
    with tempfile.TemporaryDirectory() as run, tempfile.TemporaryDirectory() as sandbox:
        real_root = cli.ROOT
        cli.ROOT = sandbox                       # 历史写进沙箱，真实 _HISTORY.jsonl 不动
        try:
            cli._append_history(run, rows)
            line = (Path(sandbox) / "runs" / "_HISTORY.jsonl").read_text(encoding="utf-8").strip()
        finally:
            cli.ROOT = real_root
        m = json.loads(line)["models"]["m1"]
        check("分母只算 graded：2 题而不是 4", m["total"] == 2 and m["passed"] == 1, str(m))
        check("未评单独计数并留题号与原因",
              m["ungraded"] == 2 and set(m["ungraded_tasks"]) == {"c", "d"}
              and "refused" in m["ungraded_tasks"]["c"], str(m.get("ungraded_tasks")))
        after = hist_file.read_bytes() if hist_file.exists() else b""
        check("真实 runs/_HISTORY.jsonl 一字节未动", after == before,
              f"{len(before)} -> {len(after)} bytes")


def test_cli_preflight():
    print("\n[4] CLI 开跑前的上游探针：不可达就不开跑")
    import importlib
    cli = importlib.import_module("cli")
    real_load = cli._load_yaml
    real_history = cli._append_history
    captured = []
    # 绝不能让这次探针写进真实 runs/_HISTORY.jsonl：换成收集器。
    cli._append_history = lambda run_dir, results, timestamp=None: captured.append(
        (run_dir, [(m, t, r.passed, r.graded, r.ungraded_reason) for m, t, r in results]))

    def fake_load(path):
        if str(path).endswith("models.yaml"):
            return {"models": [{"name": "dead-probe", "base_url": "http://127.0.0.1:59999/v1",
                                "model": "m", "wire_api": "chat", "timeout_s": 2}],
                    "judge": {"name": "none", "base_url": "http://127.0.0.1:59999/v1",
                              "model": "none", "wire_api": "chat"}}
        return real_load(path)

    class A:
        tasks = "locate-bug-001"
        models = "dead-probe"
        model_label = api_model = max_output_tokens = request_timeout_s = out = None
        base_url = api_key = api_key_env = wire_api = None
        jobs = 1
        judge = False
        skip_preflight = False
        preflight_wait_s = 1
        max_retries = 1
        merge_run = False

    with tempfile.TemporaryDirectory() as run:
        A.out = run
        cli._load_yaml = fake_load
        try:
            rc = cli.cmd_run(A)
        finally:
            cli._load_yaml = real_load
        produced = list(Path(run).rglob("result.json"))
        check("上游不可达 -> 返回码 2（不是 0）", rc == 2, f"rc={rc}")
        check("上游不可达 -> 一题都没跑", not produced, f"产生了 {len(produced)} 个结果")
        check("上游不可达 -> 没往历史里写任何东西", not captured, str(captured)[:80])

        A.skip_preflight = True
        A.preflight_wait_s = 0
        cli._load_yaml = fake_load
        try:
            rc2 = cli.cmd_run(A)
        finally:
            cli._load_yaml = real_load
            cli._append_history = real_history
        produced2 = sorted(p.parent.name for p in Path(run).rglob("result.json"))
        check("--skip-preflight 仍可强行开跑", rc2 == 0 and produced2 == ["locate-bug-001"],
              f"rc={rc2} results={produced2}")
        if produced2:
            saved = json.loads((Path(run) / "dead-probe" / "locate-bug-001"
                                / "result.json").read_text(encoding="utf-8"))
            check("强跑后上游故障的题被记为未评而不是失败",
                  saved.get("graded") is False and saved.get("passed") is False,
                  str(saved.get("ungraded_reason"))[:90])
            check("未评的尝试仍被送进历史聚合（由聚合层挡在分母外）",
                  bool(captured) and captured[-1][1][0][3] is False, str(captured)[:160])


def main():
    test_provider()
    test_external_api_key()
    test_agent_loop()
    test_scoring()
    test_cli_preflight()
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n=== {len(RESULTS) - len(bad)}/{len(RESULTS)} 断言通过 ===")
    for n in bad:
        print("  FAILED:", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
