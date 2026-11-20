#!/usr/bin/env python3
"""热调并发的行为验证：改 control.json 当场生效，且绝不打断在跑的题。

这块逻辑之所以要有测试而不是"看着对"：并发闸是**跨线程**状态，光读代码看不出缩并发时
会不会把在跑的任务掐掉，也看不出放大时会不会有线程拿不到位子。这里用假慢任务把三件事
钉住：

1. 起跑时并发 = 设定值（不会偷偷并行）；
2. 中途把 control.json 改大 → 观测到的峰值并发跟着上去；
3. 中途改小 → 在跑的题继续跑完（没有任务被中断或重复执行），只有还没开的题排队。

顺带钉住"坏文件不影响判定"：control.json 读不懂就当没变化。
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# cli.py 是脚本不是包，按路径加载（和 tools/test_infra_retry.py 同一手法）
spec = importlib.util.spec_from_file_location("agent_eval_cli", os.path.join(ROOT, "cli.py"))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

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


class Runner:
    """记录并发曲线的假任务执行器。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.live = 0
        self.peak = 0
        self.started = []
        self.finished = []

    def job(self, index, block_for=None):
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
            self.started.append(index)
        try:
            if block_for is not None:
                block_for.wait(10)     # 把某道题故意钉住，模拟"正在跑的题"
            time.sleep(0.05)
        finally:
            with self.lock:
                self.live -= 1
                self.finished.append(index)


def _write(path, jobs):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"jobs": jobs, "max_jobs": cli.CONTROL_MAX_JOBS, "source": "test"}, handle)
    os.replace(tmp, path)


def _later(delay, fn):
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()
    return timer


def _run(jobs, count, control=None, submit_hook=None, block=None):
    """跑一批假任务；闸的轮询间隔临时缩短，免得测试为默认 2 秒干等。"""
    runner = Runner()
    pool = cli.GatedPool(jobs=jobs, control_path=control, print_lock=threading.Lock())
    original = cli.CONTROL_POLL_SECONDS
    cli.CONTROL_POLL_SECONDS = 0.05
    pool.__enter__()
    try:
        if submit_hook:
            submit_hook()
        futures = [pool.submit(runner.job, i, *([block] if i == 0 and block else []))
                   for i in range(count)]
        for future in futures:
            future.result()
    finally:
        cli.CONTROL_POLL_SECONDS = original
        pool.__exit__(None, None, None)
    return runner


def gate_basics():
    print("=== 闸本身：越界值夹住 ===")
    gate = cli.ConcurrencyGate(2)
    check("超过上限夹到 CONTROL_MAX_JOBS", gate.set_permits(9999), cli.CONTROL_MAX_JOBS)
    check("0 夹回 1", gate.set_permits(0), 1)
    check("负数夹回 1", gate.set_permits(-7), 1)
    check("空闲时 running=0", gate.snapshot()["running"], 0)


def respects_starting_jobs():
    print("=== 起跑并发守设定 ===")
    r = _run(jobs=1, count=8)
    check("jobs=1 时峰值并发为 1", r.peak, 1)
    check("8 道题全部跑完且不重复", (len(r.started), len(r.finished), len(set(r.finished))),
          (8, 8, 8))


def grows_at_runtime():
    print("=== 跑起来之后加并发 ===")
    with tempfile.TemporaryDirectory() as tmp:
        control = os.path.join(tmp, "control.json")
        r = _run(jobs=1, count=24, control=control,
                 submit_hook=lambda: _later(0.25, lambda: _write(control, 4)))
        check("热调之后峰值到达设定值 4", r.peak >= 4, True)
        check("24 道题全跑完、无重复", (len(r.finished), len(set(r.finished))), (24, 24))
        check("闸把生效值回写，看板读到的就是真在跑的",
              json.load(open(control, encoding="utf-8"))["jobs"], 4)


def shrink_never_interrupts():
    print("=== 缩并发绝不打断在跑的题 ===")
    with tempfile.TemporaryDirectory() as tmp:
        control = os.path.join(tmp, "control.json")
        stuck = threading.Event()
        r = Runner()
        pool = cli.GatedPool(jobs=4, control_path=control, print_lock=threading.Lock())
        original = cli.CONTROL_POLL_SECONDS
        cli.CONTROL_POLL_SECONDS = 0.05
        pool.__enter__()
        try:
            futures = [pool.submit(r.job, 0, stuck)]
            futures += [pool.submit(r.job, i) for i in range(1, 6)]
            time.sleep(0.3)
            _write(control, 1)                    # 看板把并发收到 1
            time.sleep(0.3)
            check("收并发时 0 号题仍在跑、没被掐", (0 in r.started, 0 in r.finished),
                  (True, False))
            stuck.set()
            for future in futures:
                future.result()
        finally:
            cli.CONTROL_POLL_SECONDS = original
            pool.__exit__(None, None, None)
        check("被钉住的题最终跑完", 0 in r.finished, True)
        check("6 道题有始有终、无重复",
              (len(r.started), len(r.finished), len(set(r.finished))), (6, 6, 6))
        check("收缩后的设定留在文件里", json.load(open(control, encoding="utf-8"))["jobs"], 1)


def ceiling_is_the_endpoints_own():
    print("=== 天花板由端点决定，不是常量 ===")
    with tempfile.TemporaryDirectory() as tmp:
        control = os.path.join(tmp, "control.json")
        original = cli.CONTROL_POLL_SECONDS
        cli.CONTROL_POLL_SECONDS = 0.05
        pool = cli.GatedPool(jobs=1, control_path=control, print_lock=threading.Lock(), ceiling=4)
        pool.__enter__()
        try:
            check("起跑文件里的天花板是本轮端点的 4，不是全局 16",
                  json.load(open(control, encoding="utf-8"))["max_jobs"], 4)
            _write(control, 16)                     # 看板/手改越界
            time.sleep(0.4)
            echoed = json.load(open(control, encoding="utf-8"))
            check("越界请求被夹回 4", pool.gate.permits, 4)
            check("回写的是实际生效值和实际天花板", (echoed["jobs"], echoed["max_jobs"]), (4, 4))
            r = Runner()
            futures = [pool.submit(r.job, i) for i in range(8)]
            for future in futures:
                future.result()
            check("夹住之后峰值并发也只有 4", r.peak <= 4, True)
        finally:
            cli.CONTROL_POLL_SECONDS = original
            pool.__exit__(None, None, None)


def junk_control_is_inert():
    print("=== 坏 control.json 不能影响判定 ===")
    with tempfile.TemporaryDirectory() as tmp:
        control = os.path.join(tmp, "control.json")
        with open(control, "w", encoding="utf-8") as handle:
            handle.write("{这不是 json")           # 半成品/写坏
        r = _run(jobs=2, count=6, control=control)
        check("读不懂就当没变化，任务照常跑完", (len(r.finished), r.peak <= 2), (6, True))
    check("路径为 None 时读取返回空", cli._read_control_file(None), {})


if __name__ == "__main__":
    gate_basics()
    respects_starting_jobs()
    grows_at_runtime()
    shrink_never_interrupts()
    ceiling_is_the_endpoints_own()
    junk_control_is_inert()
    print(f"\n通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
