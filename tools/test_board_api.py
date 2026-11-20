#!/usr/bin/env python3
"""看板后端的接口契约测试：泳道推导、排队意图持久化、排序、并发热调的边界。

只在临时目录里跑，**不碰真的 runs/_BOARD.json，也不发真 run**：发车成功路径会拉起
一次真实评测（烧 GPU/API），所以这里只验到"空队列拒绝发车"和参数校验那一层，真发车
留给端到端手工确认。
"""
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import web_server  # noqa: E402

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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Client:
    def __init__(self, base):
        self.base = base

    def call(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8") or "{}"
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"error": raw}


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="board-api-")
    # 把看板状态指到临时文件：测试不许改用户的 runs/_BOARD.json
    web_server.BOARD_PATH = Path(tmp) / "_BOARD.json"
    port = free_port()
    server = web_server.ThreadingHTTPServer(("127.0.0.1", port), web_server.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = Client(f"http://127.0.0.1:{port}")
    try:
        run_checks(client, tmp)
    finally:
        server.shutdown()
        server.server_close()
    print(f"\n通过 {PASS} / {PASS + FAIL}")
    return 1 if FAIL else 0


def run_checks(client: Client, tmp: str) -> None:
    ids = [task["id"] for task in web_server.MANAGER.tasks]
    print("=== GET /api/board ===")
    status, payload = client.call("GET", "/api/board")
    check("200", status, 200)
    check("三泳道加起来等于全部题数", sum(payload["counts"].values()), len(ids))
    check("卡片数等于题数", len(payload["cards"]), len(ids))
    check("没有 run 在跑时排队列为空", payload["counts"]["queue"], 0)
    check("默认按手动顺序", payload["board"]["sort"], "manual")
    check("卡片带题面难度与实测两块", {"difficulty", "measure"} <= set(payload["cards"][ids[0]]),
          True)
    check("并发实盘在无 run 时不适用", payload["concurrency"]["applicable"], False)

    print("=== 实测分母：只有对当前题面成立的判定才算数 ===")
    bad = [(tid, c["measure"]) for tid, c in payload["cards"].items() if c["measure"]
           and (c["measure"]["gradedAttempts"] + c["measure"]["ungradedAttempts"]
                + c["measure"]["staleAttempts"]) != c["measure"]["attempts"]]
    check("全部题：已判定 + 未评 + 待重测 = 总尝试", bad, [])
    with_stale = [(tid, c["measure"]) for tid, c in payload["cards"].items() if c["measure"].get("staleAttempts")]
    check("确实有带 stale 的历史可验（没有就说明扫描坏了）", bool(with_stale), True)
    check("带 stale 的题都没有把 stale 通过算进分子",
          [(tid, row["passes"], row["gradedAttempts"]) for tid, row in with_stale
           if row["passes"] > row["gradedAttempts"]], [])
    # 整题只剩旧版本判定时 passRate 就是 None（没有对当前题面成立的判定，就不该有实测值），
    # 所以这里只挑一个真有分母的样例打印。
    sample = next(((tid, row) for tid, row in with_stale if row["passRate"] is not None), None)
    if sample:
        tid, row = sample
        diluted = row["passes"] / max(1, row["gradedAttempts"] + row["staleAttempts"])
        print(f"       （{tid}：{row['passes']}/{row['gradedAttempts']} 已判定 = "
              f"{row['passRate']:.1%}；把 {row['staleAttempts']} 次旧版本混进去会稀释成 {diluted:.1%}）")

    print("=== 排队意图 ===")
    pick = ids[:3]
    status, after = client.call("POST", "/api/board", {"queued": pick})
    check("写入 200", status, 200)
    check("三题进排队", after["counts"]["queue"], 3)
    check("排队的确是那三题", sorted(after["lanes"]["queue"]), sorted(pick))
    status, again = client.call("GET", "/api/board")
    check("刷新之后还在（持久化）", again["counts"]["queue"], 3)
    client.call("POST", "/api/board", {"queued": []})
    _, cleared = client.call("GET", "/api/board")
    check("清空排队生效", cleared["counts"]["queue"], 0)

    print("=== 非法输入必须整条拒绝 ===")
    code, body = client.call("POST", "/api/board", {"queued": ["no-such-task"]})
    check("未知题号 400", code, 400)
    check("错误可读", "未知任务" in body.get("error", ""), True)
    code, _ = client.call("POST", "/api/board", {"queued": [ids[0], ids[0]]})
    check("重复题号 400", code, 400)
    code, _ = client.call("POST", "/api/board", {"difficulty": {ids[0]: "impossible"}})
    check("非法难度 400", code, 400)
    code, _ = client.call("POST", "/api/board", {"sort": "random"})
    check("非法排序 400", code, 400)
    code, _ = client.call("POST", "/api/board", {"queued": [ids[0]]})
    _, ok_state = client.call("GET", "/api/board")
    check("前面失败的请求不该留下半成品", ok_state["counts"]["queue"], 1)

    print("=== 顺序与难度排序 ===")
    client.call("POST", "/api/board", {"queued": []})
    order = list(reversed(ids))
    client.call("POST", "/api/board", {"order": order})
    _, by_manual = client.call("GET", "/api/board")
    check("手动顺序被记住", by_manual["lanes"]["done"][:3], order[:3])
    hard_ids = [t["id"] for t in web_server.MANAGER.tasks if t["difficulty"] == "hard"]
    client.call("POST", "/api/board", {"sort": "difficulty_desc"})
    _, by_diff = client.call("GET", "/api/board")
    head = by_diff["lanes"]["done"][:len(hard_ids)]
    check("难度降序时最前面全是 hard", set(head) >= set(head) and all(h in hard_ids for h in head),
          True)
    client.call("POST", "/api/board", {"sort": "measured_asc"})
    _, by_meas = client.call("GET", "/api/board")
    lane = by_meas["lanes"]["done"]
    rates = [by_meas["cards"][tid]["measure"].get("passRate") for tid in lane]
    measured = [r for r in rates if r is not None]
    check("实测升序：通过率单调不增", measured == sorted(measured, reverse=True), True)
    check("实测升序：没数据的垫底而不是冒充易题",
          all(r is None for r in rates[len(measured):]), True)
    print(f"       （实测前五通过率：{rates[:5]}，有样本 {len(measured)}/{len(lane)}）")
    client.call("POST", "/api/board", {"sort": "measured_desc"})
    _, by_meas_desc = client.call("GET", "/api/board")
    desc = [by_meas_desc["cards"][tid]["measure"].get("passRate") for tid in by_meas_desc["lanes"]["done"]]
    desc_rates = [r for r in desc if r is not None]
    check("实测降序：通过率单调不减（难题在前）", desc_rates == sorted(desc_rates), True)
    client.call("POST", "/api/board", {"sort": "manual", "order": []})

    print("=== 难度覆盖 ===")
    client.call("POST", "/api/board", {"difficulty": {ids[0]: "hard"}})
    _, overridden = client.call("GET", "/api/board")
    check("覆盖生效", overridden["cards"][ids[0]]["difficultyEffective"], "hard")
    check("标明是被改过的", overridden["cards"][ids[0]]["difficultyOverridden"], True)
    client.call("POST", "/api/board", {"clearDifficulty": ids[0]})
    _, back = client.call("GET", "/api/board")
    check("清除后回到题面值",
          back["cards"][ids[0]]["difficultyEffective"] == back["cards"][ids[0]]["difficulty"], True)

    print("=== 并发热调的边界 ===")
    code, body = client.call("POST", "/api/concurrency", {"jobs": 3})
    check("没有 run 在跑时拒绝", code, 409)
    check("说清楚为什么拒绝", "没有正在运行" in body.get("error", ""), True)
    code, _ = client.call("POST", "/api/concurrency", {"jobs": "三"})
    check("非整数 400", code, 400)
    ceiling = web_server.board_ceiling()
    code, _ = client.call("POST", "/api/board", {"jobs": ceiling + 5})
    check("看板设定超上限 400", code, 400)
    code, saved = client.call("POST", "/api/board", {"jobs": 2})
    check("看板设定在范围内可存", code, 200)
    check("存下了并发", saved["board"]["jobs"], 2)

    print("=== 有 run 在跑时：热调写进 control.json，泳道由实况推导 ===")
    fake_run = Path(tmp) / "fake-run"
    fake_run.mkdir()
    manager = web_server.MANAGER
    with manager.lock:
        saved_state = dict(manager.state)
        manager.state.update({
            "id": "fake-run", "status": "running", "outputDir": str(fake_run),
            "profile": "qwen3-27b-local", "model": "fake", "jobs": 1,
            "currentTasks": [ids[0]],
            "taskRuns": [{"index": 1, "taskId": ids[0], "title": ids[0], "category": "debug",
                          "status": "running", "result": None, "steps": None, "tokens": None,
                          "elapsedSeconds": None, "failure": None},
                         {"index": 2, "taskId": ids[1], "title": ids[1], "category": "debug",
                          "status": "pending", "result": None, "steps": None, "tokens": None,
                          "elapsedSeconds": None, "failure": None}],
        })
    try:
        code, body = client.call("POST", "/api/concurrency", {"jobs": 3})
        check("热调 200", code, 200)
        written = json.loads((fake_run / "control.json").read_text(encoding="utf-8"))
        check("control.json 被写入", (written.get("jobs"), written.get("source")), (3, "web"))
        check("上限按当前端点记", written.get("max_jobs"), 4)
        check("响应里带实盘", body["concurrency"]["jobs"], 3)
        code, _ = client.call("POST", "/api/concurrency", {"jobs": 9})
        check("超过本地端点上限 400", code, 400)
        _, board = client.call("GET", "/api/board")
        check("运行中这一列 = 在跑 + 本轮待跑", sorted(board["lanes"]["running"]), sorted([ids[0], ids[1]]))
        # 即使把在跑的题拖进排队意图，泳道也必须按实况归位：不允许两份真相
        client.call("POST", "/api/board", {"queued": [ids[0], ids[2]]})
        _, board = client.call("GET", "/api/board")
        check("在跑的题不会被算成排队", board["lanes"]["queue"], [ids[2]])
        check("并发滑条显示的是实盘而不是发车时的值",
              (board["concurrency"]["jobs"], board["run"]["startedJobs"]), (3, 1))
        code, launched = client.call("POST", "/api/board/launch", {"profile": "qwen3-27b-local"})
        check("已有 run 时不排第二个", code, 409)
        # 本轮还没开跑的题：cli 只在开题那一行才记 taskRuns，所以"清单里有、记录里没有"
        # 才是真相。它们既不能算已完成（那道题从没跑过），也不能算排队（已经在这一轮里了）。
        (fake_run / "manifest.json").write_text(json.dumps(
            {"tasks": ids[:5], "models": ["fake"], "jobs": 3}), encoding="utf-8")
        with manager.lock:
            manager.state["taskRuns"] = manager.state["taskRuns"][:1]   # 只留 ids[0] 在跑
            manager.state["currentTasks"] = [ids[0]]
        _, board = client.call("GET", "/api/board")
        check("本轮待跑 = 清单里还没开题的题", board["lanes"]["waiting"], ids[1:5])
        check("没开跑的题不会被算进已完成", ids[1] in board["lanes"]["done"], False)
        check("待跑卡片带本轮顺序", [board["cards"][t]["runPosition"] for t in ids[1:5]], [2, 3, 4, 5])
        check("待跑列即使按难度排序也保持 run 自己的顺序",
              client.call("POST", "/api/board", {"sort": "measured_desc"})[1]["lanes"]["waiting"],
              ids[1:5])
        check("四列加起来还是全部题数", sum(board["counts"].values()), len(ids))
        check("排队意图不会被本轮待跑抢走", board["lanes"]["queue"], [])
        client.call("POST", "/api/board", {"sort": "manual"})
    finally:
        with manager.lock:
            manager.state.clear()
            manager.state.update(saved_state)

    print("=== 发车前的守门 ===")
    client.call("POST", "/api/board", {"queued": []})
    code, body = client.call("POST", "/api/board/launch", {"profile": "qwen3-27b-local"})
    check("空队列不发车", code, 400)
    check("给的是人话", "排队是空的" in body.get("error", ""), True)
    code, body = client.call("POST", "/api/runs", {"profile": "no-such-profile", "tasks": [ids[0]]})
    check("未知 profile 400", code, 400)
    code, body = client.call("POST", "/api/runs",
                             {"profile": "qwen3-27b-local", "tasks": [ids[0], "ghost-task"]})
    check("清单里混入未知题号 400", code, 400)

    print("=== 卡片详情：单次运行的判定明细与轨迹 ===")
    with_data = [tid for tid, card in payload["cards"].items()
                 if (card["measure"].get("recent") or [])]
    check("有历史结果时带逐次尝试", bool(with_data), True)
    first = payload["cards"][with_data[0]]["measure"]
    attempt = first["recent"][0]
    check("逐次尝试足够点开（run + model + 结论）",
          {"runId", "model", "passed", "graded", "steps", "tokens"} <= set(attempt), True)
    check("轮询里不带失败证据文本（点开时才按 run 取）", "failure" in attempt, False)
    check("证据留在 last 里", "failure" in (first["last"] or {}), True)
    code, detail = client.call("GET", f"/api/runs/{attempt['runId']}/tasks/{with_data[0]}")
    check("单题详情 200", code, 200)
    entry = (detail.get("attempts") or [{}])[0]
    check("详情里有判定项", bool(entry.get("checks")), True)
    check("详情里有逐步轨迹", bool((entry.get("trajectory") or {}).get("steps")), True)
    check("轨迹步带工具调用", any(s.get("toolCalls") for s in entry["trajectory"]["steps"]), True)
    check("越界题号被挡住", client.call("GET", f"/api/runs/{attempt['runId']}/tasks/no-such-task")[0], 404)
    check("越界 run 号被挡住", client.call("GET", "/api/runs/no-such-run/tasks/" + with_data[0])[0], 404)
    check("lanes 只给 id，卡片内容不重复一遍",
          set(payload) >= {"lanes", "cards"} and "lanes_detail" not in payload, True)

    print("=== 静态资源与旧接口没被新路由挡住 ===")
    code, _ = client.call("GET", "/api/config")
    check("旧 /api/config 仍 200", code, 200)
    code, _ = client.call("GET", "/api/runs/current")
    check("旧 /api/runs/current 仍 200", code, 200)
    code, cfg = client.call("GET", "/api/config")
    check("每个 profile 带自己的并发天花板", all("maxJobs" in m for m in cfg["models"]), True)
    # 天花板与来源分层按**真实上游**算：回环地址上挂着转发器（upstream_url）时那一行仍然
    # 是云端 API。照地址判会把付费 API 压回 4 个 worker，还会把它标成"本地量化"进榜。
    local = [m for m in cfg["models"] if m["kind"] == "local"]
    check("本地端点天花板是 4", local and all(m["maxJobs"] == 4 for m in local), True)
    fronted = [m for m in cfg["models"]
               if m["endpoint"].startswith("http://127.0.0.1") and m.get("upstreamUrl")]
    check("走本机转发器的云端端点仍是外部 16",
          fronted and all(m["kind"] == "external" and m["maxJobs"] == 16 for m in fronted),
          True)
    check("面板临时改地址时按新地址算上游",
          web_server.classify_target({"endpoint": "http://127.0.0.1:18443/v1",
                                      "upstreamUrl": "https://example.com/v1"},
                                     "https://api.example.com/v1"),
          "https://api.example.com/v1")
    check("续跑认得出接入转发器之前的历史 run",
          (web_server.profile_for_runtime(
              [{"name": "luna-6", "endpoint": "http://127.0.0.1:18443/v1",
                "upstreamUrl": "https://example.com/v1"}],
              {"base_url": "https://example.com/v1"}) or {}).get("name"),
          "luna-6")


if __name__ == "__main__":
    sys.exit(main())
