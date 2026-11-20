#!/usr/bin/env python3
"""agent-eval CLI：run / report / rejudge / history。"""
import argparse
import os
import sys
import json
import glob
import hashlib
import re
import signal
import shutil
import time
import yaml
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from threading import Condition, Event, Lock, Thread
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evalkit.schema import TaskSpec, EvalResult  # noqa: E402
from evalkit.provider import ConfigError, ModelProvider  # noqa: E402
from evalkit.envfile import load_env_file  # noqa: E402
from evalkit.evaluator import run_task  # noqa: E402
from evalkit.judge import (JUDGE_DEFAULT, Judge, judge_any_allowed, judge_identity,  # noqa: E402
                           judge_key_source, parse_judge_choice, resolve_judge_profile)
from evalkit.rejudge import list_attempts, rejudge_attempt  # noqa: E402
from evalkit.fingerprint import task_fingerprint  # noqa: E402


def _load_yaml(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def discover_tasks(tasks_dir, filt=None):
    out = []
    for yf in sorted(glob.glob(os.path.join(tasks_dir, "**", "*.yaml"), recursive=True)):
        t = TaskSpec(**_load_yaml(yf))
        if not filt:
            out.append(t)
            continue
        if any(t.id == f or t.category == f or f in t.tags for f in filt):
            out.append(t)
    return out


def list_banks():
    """banks/*.yaml → {name: spec}，按名字排序。"""
    banks = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "banks", "*.yaml"))):
        b = _load_yaml(p)
        name = b.get("name") or os.path.basename(p)[:-5]
        banks[name] = b
    return banks


def resolve_bank(name):
    """取题库的任务 id 列表；名字不存在直接报错并列出可用题库。"""
    banks = list_banks()
    if name not in banks:
        avail = ", ".join(sorted(banks)) or "(空，先用 tools/build_bank.py 生成)"
        raise SystemExit(f"未知题库 '{name}'。可用：{avail}")
    ids = [str(x).strip() for x in (banks[name].get("tasks") or []) if str(x).strip()]
    if not ids:
        raise SystemExit(f"题库 {name} 里没有 tasks 条目")
    return ids


def _append_history(run_dir, results, timestamp=None):
    """按 run id 幂等写入关键指标，用于跨 run 对比和单题重跑。

    分母只算 **graded** 的尝试：上游连不上（重试用尽）和"带 rubric 却没评成"
    都不是一次能力测量，混进分母就等于让 leaderboard 记录"服务器哪天活着"。
    未评的数量与题目清单一起写进历史，缺考不可见才是更大的问题。
    """
    agg = defaultdict(list)
    for mname, tid, r in results:
        agg[mname].append(r)
    models = {}
    for mname, rs in agg.items():
        n_all = len(rs)
        if not n_all:
            continue
        graded = [r for r in rs if getattr(r, "graded", True)]
        ungraded = [r for r in rs if not getattr(r, "graded", True)]
        n = len(graded)
        p = sum(r.passed for r in graded)
        dim_keys = sorted({d for r in graded for d in r.scores})
        dims = {d: round(sum(r.scores[d] for r in graded if d in r.scores) /
                         max(1, sum(1 for r in graded if d in r.scores)), 3) for d in dim_keys}
        stats = graded or rs          # 未评的尝试是残缺轨迹，会污染步数/耗时口径
        models[mname] = {
            "passed": p, "total": n, "rate": round(p / n, 3) if n else 0.0,
            "ungraded": len(ungraded),
            "ungraded_tasks": {r.task_id: (getattr(r, "ungraded_reason", None) or "")[:160]
                               for r in ungraded},
            "dims": dims,
            "avg_steps": round(sum(r.efficiency.steps for r in stats) / len(stats), 1),
            "avg_tokens": round(sum(r.efficiency.tokens for r in stats) / len(stats)),
            "avg_elapsed_s": round(sum(r.efficiency.elapsed_s for r in stats) / len(stats), 1),
            "tasks": {r.task_id: {"passed": r.passed, "steps": r.efficiency.steps,
                                  "tokens": r.efficiency.tokens,
                                  "graded": bool(getattr(r, "graded", True)),
                                  "judge": (r.judge or {}).get("score") if r.judge else None}
                      for r in rs},
        }
    entry = {"run_id": os.path.basename(run_dir.rstrip("/")),
             "run_dir": run_dir,
             "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "models": models}
    hist = os.path.join(ROOT, "runs", "_HISTORY.jsonl")
    os.makedirs(os.path.dirname(hist), exist_ok=True)
    lines = []
    replaced = False
    if os.path.exists(hist):
        for line in open(hist, encoding="utf-8"):
            if not line.strip():
                continue
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line.rstrip("\n"))
                continue
            if old.get("run_id") == entry["run_id"]:
                if not replaced:
                    if timestamp is None and old.get("timestamp"):
                        entry["timestamp"] = old["timestamp"]
                    lines.append(json.dumps(entry, ensure_ascii=False))
                    replaced = True
                continue
            lines.append(line.rstrip("\n"))
    if not replaced:
        lines.append(json.dumps(entry, ensure_ascii=False))
    temporary = hist + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(temporary, hist)
    print(f"[history] {'已更新' if replaced else '已记录'} → {hist}")


def _endpoint_kind(base_url: str) -> str:
    """local = 回环地址上的自建推理服务；external = 任何别的主机。

    只用于给 run 打来源标签（榜单与报告要能看出这一行是不是云端 API），
    不做任何准入判断——本套件允许把外部 API 当被测对象跑。
    """
    host = (urlsplit(str(base_url or "")).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0", ""):
        return "local"
    if host.startswith("127.") or host.startswith("::ffff:127."):
        return "local"
    return "external"


def _provenance_url(cfg: dict) -> str:
    """端点性质按**真实上游**算，不按转发器。

    外部 API 指到本机亲和前置（http://127.0.0.1:18443，见
    （内部缓存专项报告，未随仓库发布））只是换一条出口，跑分仍然是云端 API
    的。若照回环地址标成 local，榜单就会把付费 API 那一行混进本地量化那一层比。
    models.yaml 用 upstream_url 声明真实上游；没写就按 base_url 算。
    """
    return str(cfg.get("upstream_url") or cfg.get("base_url") or "")


# ---------- 实时事件（控制台的"刷屏"）----------
# 契约：一行一条 ``[ev] {json}``，消费方是 web_server 的 _consume_progress。字段：
#   task 题目号 / model 模型名 / kind 事件类型 / 其余按类型：
#   reasoning|text -> text；tool_call -> name,arguments；tool_result -> name,ok,observation；
#   step -> idx,max_steps；step_end -> idx,latency_ms,tokens,tool_calls
#
# 为什么要节流：推理模型一道题能吐几千个 delta，逐条打行的话 stdout 本身就成瓶颈，
# 而"看得爽"绝不该换"跑得慢"。所以文本按 ~60 字或 ~0.3 秒合并一次，结构化事件
# （工具调用/工具结果/步骤）则先冲干净缓冲区再发，保证看屏时顺序不乱。
EV_FLUSH_CHARS = 60
EV_FLUSH_SECONDS = 0.3
EV_TEXT_LIMIT = 400


class EventSink:
    """把单个任务的事件写成 ``[ev] …`` 行；只在本轮开了 --events 时才装上。"""

    def __init__(self, task_id: str, model: str):
        self.task_id = task_id
        self.model = model
        self._buffer: dict[str, list[str]] = {"reasoning": [], "text": []}
        self._last = time.monotonic()
        self._lock = Lock()

    def _write(self, payload: dict) -> None:
        print("[ev] " + json.dumps(payload, ensure_ascii=False, default=str), flush=True)

    def flush(self) -> None:
        for channel in ("reasoning", "text"):
            chunk = "".join(self._buffer[channel])
            self._buffer[channel] = []
            if chunk:
                self._write({"task": self.task_id, "model": self.model,
                             "kind": channel, "text": chunk[:EV_TEXT_LIMIT]})
        self._last = time.monotonic()

    def __call__(self, kind: str, payload: dict) -> None:
        with self._lock:
            if kind in ("reasoning", "text"):
                self._buffer[kind].append(str(payload.get("text", "")))
                pending = len("".join(self._buffer[kind]))
                if pending >= EV_FLUSH_CHARS or time.monotonic() - self._last >= EV_FLUSH_SECONDS:
                    self.flush()
                return
            self.flush()
            line = {"task": self.task_id, "model": self.model, "kind": kind}
            line.update(payload)
            self._write(line)


def _load_run_results(run_dir):
    """Load the current result cut for a run after incremental retries."""
    results = []
    for result_path in glob.glob(os.path.join(run_dir, "*", "*", "result.json")):
        try:
            result = EvalResult(**_load_yaml(result_path))
        except Exception:  # noqa: BLE001
            continue
        model = os.path.basename(os.path.dirname(os.path.dirname(result_path)))
        results.append((model, result.task_id, result))
    return sorted(results, key=lambda item: (item[0], item[1]))


# 并发闸的硬上限：外部 API 的配额在服务端，16 已经是人为瓶颈；本地单服务再高只是排队。
CONTROL_MAX_JOBS = 16
CONTROL_POLL_SECONDS = 2.0


class ConcurrencyGate:
    """可以边跑边改大小的并发闸。

    语义：每道题**开始之前**在闸上等空位，拿到位子就跑完再还。所以调小并发只会让
    "还没开的题"排队，绝不打断已经在跑的题——掐掉在跑的题等于把它的结果一起丢掉，
    那是拿真金白银的 token 换手感。

    `ceiling` 是这一轮端点能吃下的上限（本地单推理槽=4，外部 API=16），热调也越不过
    它：开跑前校验挡得住的事情，热调时同样得挡住，否则看板就是个绕过安全阀的后门。
    """

    def __init__(self, permits: int, ceiling: int = CONTROL_MAX_JOBS):
        self._ceiling = max(1, min(CONTROL_MAX_JOBS, int(ceiling)))
        self._permits = max(1, min(self._ceiling, int(permits)))
        self._running = 0
        self._cv = Condition()

    @property
    def permits(self) -> int:
        return self._permits

    @property
    def ceiling(self) -> int:
        return self._ceiling

    def set_permits(self, value: int) -> int:
        """把并发改成 value，返回实际改成的值（越界就夹住）。"""
        target = max(1, min(self._ceiling, int(value)))
        with self._cv:
            changed = target != self._permits
            self._permits = target
            if changed:
                self._cv.notify_all()
        return target

    @contextmanager
    def slot(self):
        """`with gate.slot():` —— 开始一道题前占一个并发位，跑完归还。"""
        self._acquire()
        try:
            yield None
        finally:
            self._release()

    def _acquire(self) -> None:
        with self._cv:
            while self._running >= self._permits:
                self._cv.wait()
            self._running += 1

    def _release(self) -> None:
        with self._cv:
            self._running -= 1
            self._cv.notify_all()

    def snapshot(self) -> dict:
        with self._cv:
            return {"permits": self._permits, "running": self._running}


class GatedPool:
    """按 control.json 热调并发的执行器，接口对齐 ThreadPoolExecutor 的 submit。

    为什么要单独一层：`ThreadPoolExecutor.max_workers` 是构造时定死的，而任务一旦
    submit 就全排在它内部队列里，外部既改不动额度也收不回排队任务。这里的做法是把
    线程数一次给到上限、真正的额度放在 `ConcurrencyGate` 上——闸在 1..16 之间随便调，
    不需要重建池，也不需要取消任何排队任务。
    """

    def __init__(self, jobs: int, control_path: str | None = None, print_lock=None,
                 ceiling: int = CONTROL_MAX_JOBS):
        self.gate = ConcurrencyGate(jobs, ceiling)
        self.control_path = control_path
        self.print_lock = print_lock or Lock()
        self.inner = ThreadPoolExecutor(max_workers=self.gate.ceiling,
                                        thread_name_prefix="agent-eval")
        self._stop = Event()
        self._watcher = None

    def __enter__(self):
        if self.control_path:
            self._write_initial()
            self._watcher = Thread(target=self._watch_control, daemon=True,
                                   name="agent-eval-control")
            self._watcher.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self.inner.shutdown(wait=True)
        return False

    def submit(self, fn, *args):
        return self.inner.submit(self._gated, fn, *args)

    def _gated(self, fn, *args):
        with self.gate.slot():
            return fn(*args)

    def _write_initial(self) -> None:
        self._write_control({"jobs": self.gate.permits, "max_jobs": self.gate.ceiling,
                             "source": "cli"}, quiet=True)

    def _write_control(self, payload: dict, quiet: bool = False) -> None:
        """把当前设定回写进 control.json，让看板读到的就是真在生效的值。

        写失败一律忽略：control.json 是加速器不是账本，不能因为它把评测跑挂。
        """
        if not self.control_path:
            return
        tmp = f"{self.control_path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp, self.control_path)
        except OSError as exc:
            if not quiet:
                with self.print_lock:
                    print(f"[concurrency] 回写 {self.control_path} 失败：{exc}", flush=True)

    def _watch_control(self) -> None:
        while not self._stop.wait(CONTROL_POLL_SECONDS):
            payload = _read_control_file(self.control_path)
            if not payload:
                continue
            wanted = payload.get("jobs")
            if not isinstance(wanted, int):
                continue
            before = self.gate.permits
            after = self.gate.set_permits(wanted)
            if after != before:
                snap = self.gate.snapshot()
                with self.print_lock:
                    print(f"[concurrency] jobs {before} -> {after}（{snap['running']} 道题在跑，"
                          f"未开题按新并发排队）", flush=True)
            if payload.get("jobs") != self.gate.permits:
                # 回写的是闸**实际接受**的值和它真正的天花板：看板写 16、本地只能跑 4 时，
                # 文件里必须留下 4，否则界面会一直显示一个没在生效的数字。
                self._write_control({"jobs": self.gate.permits,
                                     "max_jobs": self.gate.ceiling, "source": "cli"})


def _read_control_file(path: str | None) -> dict:
    """读期望设定。文件缺失/半成品/不是 JSON 都按"没变化"处理，绝不因此中断跑批。"""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def cmd_run(args):
    # 外部端点的密钥从仓库根的 .env 读（已存在的环境变量优先）：models.yaml 里只写
    # "${MIMO_API_KEY}" 这种引用，密钥本身不入库。
    load_env_file(os.path.join(ROOT, ".env"))
    if args.max_output_tokens is not None and not 256 <= args.max_output_tokens <= 262144:
        print("--max-output-tokens must be 256..262144", file=sys.stderr)
        return 1
    if args.request_timeout_s is not None and not 10 <= args.request_timeout_s <= 3600:
        print("--request-timeout-s must be 10..3600", file=sys.stderr)
        return 1
    data = _load_yaml(os.path.join(ROOT, "models.yaml"))
    # 配好，交出来的是一堆「未评」，那比不跑还坑。
    # 裁判是一个**可选**闸门，而且默认开着：不点名就用 models.yaml 里 judge.default 那一档
    # （现默认 mimo-flash）。两类失败分开处理——
    #   * 没点名（含不带档名的 --judge）：降成"本轮不开裁判"并说清为什么，别拦住整轮跑批；
    #   * 点了名：硬拦。指名要那把尺的时候，"静默变成不算分"比不发车更坏。
    # 档案与密钥都在开跑前验完：跑完整轮才发现裁判没配好，交出来的是一堆未评。
    # 口径只认 parse_judge_choice 一处：True/"default"/省略=开+默认档，False/"off"/""/None=关，
    # 其余是档案名。老调用方（含 tools/ 里的测试夹具）传 True/False 也走这同一个翻译。
    parsed_judge = parse_judge_choice(args.judge)
    judge_cfg = None
    asked_by_name = parsed_judge not in (None, JUDGE_DEFAULT)
    try:
        if parsed_judge is not None:
            judge_cfg = resolve_judge_profile(
                data, None if parsed_judge == JUDGE_DEFAULT else parsed_judge)
    except ValueError as exc:
        if asked_by_name:
            print(str(exc), file=sys.stderr)
            return 2
        print(f'[judge] {exc} → 本轮不启用裁判（带 rubric 的题记「未评」）', flush=True)
        judge_cfg = None
    if judge_cfg is not None:
        key_ready, key_from = judge_key_source(judge_cfg)
        if not key_ready:
            name = judge_cfg.get('name', '?')
            var = key_from[4:] if key_from.startswith('env:') else key_from
            if asked_by_name:
                print(f'裁判档案 {name} 的密钥不可用（{key_from}）：把 {var} 写进仓库根的 .env '
                      f'或先 export；不要判分就加 --no-judge', file=sys.stderr)
                return 2
            print(f'[judge] 默认档 {name} 缺密钥（{key_from}）→ 本轮不启用裁判；'
                  f'要用它就写进 .env 或显式 --judge {name}', flush=True)
            judge_cfg = None
    if args.models in (None, "all", ""):
        wanted = [m["name"] for m in data["models"]]
    else:
        wanted = [x.strip() for x in args.models.split(",") if x.strip()]
    provider_configs = {m["name"]: m for m in data["models"] if m["name"] in wanted}
    if not provider_configs:
        print("no matching models", file=sys.stderr)
        return 1
    if (args.model_label or args.api_model or args.max_output_tokens or args.request_timeout_s
            or args.base_url or args.api_key or args.api_key_env or args.wire_api):
        if len(provider_configs) != 1:
            print("runtime model overrides require exactly one --models profile", file=sys.stderr)
            return 1
        config = dict(next(iter(provider_configs.values())))
        if args.model_label:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.model_label):
                print("invalid --model-label; use 1-64 letters, digits, dot, underscore or hyphen", file=sys.stderr)
                return 1
            config["name"] = args.model_label
        if args.api_model:
            config["model"] = args.api_model
        if args.base_url:
            config["base_url"] = args.base_url
            # 临时换地址就是换了真实上游，profile 里那条 upstream_url 立刻作废。
            config.pop("upstream_url", None)
        if args.wire_api:
            if args.wire_api not in ("chat", "responses"):
                print("--wire-api 只支持 chat 或 responses", file=sys.stderr)
                return 1
            config["wire_api"] = args.wire_api
        if args.api_key and args.api_key_env:
            print("--api-key 与 --api-key-env 二选一（推荐后者：密钥不进 ps 与 shell 历史）", file=sys.stderr)
            return 1
        if args.api_key_env:
            secret = os.environ.get(args.api_key_env, "")
            if not secret:
                print(f"环境变量 {args.api_key_env} 未设置或为空；密钥缺失时不开跑，"
                      f"否则会退化成一次必然 401 的上游故障", file=sys.stderr)
                return 2
            config["api_key"] = secret
            config["_key_source"] = f"env:{args.api_key_env}"
        elif args.api_key:
            config["api_key"] = args.api_key
            config["_key_source"] = "argv"
        if args.max_output_tokens:
            config["max_output_tokens"] = args.max_output_tokens
        if args.request_timeout_s:
            config["timeout_s"] = args.request_timeout_s
        provider_configs = {config["name"]: config}
    for cfg in provider_configs.values():
        cfg["max_retries"] = args.max_retries
        # 密钥来源也记一笔：报告里要能看出这次跑的是"本机临时 export"还是 ".env 里的常驻
        # key"。只记变量名，不记值。
        if not cfg.get("_key_source"):
            raw = str(cfg.get("api_key", ""))
            if raw.startswith("${") and raw.endswith("}"):
                cfg["_key_source"] = f"env:{raw[2:-1]}"
    # 并发上限按端点性质给：本地单一推理服务（一台 GPU 串行解码）开 4 个 worker 只是在排队，
    # 再高只会把上下文挤爆；外部 API 的配额在服务端，云端跑一整行 50 题时 1..4 是人为瓶颈。
    kinds = {_endpoint_kind(_provenance_url(cfg)) for cfg in provider_configs.values()}
    max_jobs = 4 if kinds == {"local"} else 16
    if not 1 <= args.jobs <= max_jobs:
        print(f"--jobs 必须在 1..{max_jobs} 之间（本轮端点：{', '.join(sorted(kinds))}"
              f"{'；本地端点上限 4，外部 API 上限 16' if max_jobs == 4 else ''}）", file=sys.stderr)
        return 2
    tasks = discover_tasks(os.path.join(ROOT, "tasks"), args.tasks.split(",") if args.tasks else None)
    bank_note = None
    if getattr(args, "bank", None):
        bank_ids = set(resolve_bank(args.bank))
        all_ids = {t.id for t in discover_tasks(os.path.join(ROOT, "tasks"))}
        stale = sorted(bank_ids - all_ids)
        if stale:
            print(f"[bank] 警告：题库里有 {len(stale)} 个 id 在 tasks/ 中不存在，已忽略：{', '.join(stale)}",
                  file=sys.stderr)
        tasks = [t for t in tasks if t.id in bank_ids]
        bank_note = f"题库 {args.bank}: {len(tasks)} 题"
    if not tasks:
        print("no matching tasks", file=sys.stderr)
        return 1
    if bank_note:
        print(f"[bank] {bank_note}")
    if not args.skip_preflight:
        # 50 道题 × 一个模型的分数可以被"服务没起来"整行吃掉（实测有跑批夜 25/65
        # 次是 connection refused）。开跑前只花几秒探一下，比跑完再发现全废便宜得多。
        unreachable = {}
        for mname, config in provider_configs.items():
            probe = ModelProvider(config)
            deadline = time.monotonic() + args.preflight_wait_s
            ok, detail = False, "not probed"
            try:
                while True:
                    ok, detail = probe.health()
                    if ok or time.monotonic() >= deadline:
                        break
                    time.sleep(min(5.0, max(0.5, deadline - time.monotonic())))
            except ConfigError as exc:
                # 配置确定性错（model id 不存在）：再等多久也不会变，立刻拒绝。
                print(f"[preflight] {mname}: CONFIG {exc}", flush=True)
                print(f"这次请求本身就不对（{exc}）；修正后重发，别用 --skip-preflight 硬跑——"
                      f"那会把整轮写成看上游脸色的分数。", file=sys.stderr)
                return 2
            print(f"[preflight] {mname}: {'OK' if ok else 'UNREACHABLE'} {detail}", flush=True)
            if not ok:
                unreachable[mname] = detail
        if unreachable:
            print("上游不可达，未开跑（避免把基础设施故障记成模型分数）："
                  + ", ".join(f"{k}={v}" for k, v in unreachable.items()), file=sys.stderr)
            print("确认要继续可用 --skip-preflight；这些尝试之后会被记为\"未评\"。", file=sys.stderr)
            return 2
    if not args.skip_preflight and judge_cfg is not None:
        # 裁判那头也要探一下：尺子不在家时开跑，这一轮带 rubric 的题全变未评——那和
        # 被测服务没起来是同一类事，不该被记成模型分数。
        probe = ModelProvider(judge_cfg)
        try:
            ok, detail = probe.health()
        except ConfigError as exc:
            print(f"[preflight] judge {judge_identity(judge_cfg)}: CONFIG {exc}", flush=True)
            print("裁判这把尺本身就不对：改 models.yaml 的 judge.profiles（或 --judge 换一档）。",
                  file=sys.stderr)
            return 2
        print(f"[preflight] judge {judge_identity(judge_cfg)}: "
              f"{'OK' if ok else 'UNREACHABLE'} {detail}", flush=True)
        if not ok:
            print(f"裁判端点不可达，未开跑：{judge_identity(judge_cfg)} 打不通，带 rubric 的题"
                  f"会整批记\"未评\"。先修档案；确认可用 --skip-preflight 硬跑，"
                  f"或干脆 --no-judge。", file=sys.stderr)
            return 2
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.out or os.path.join(ROOT, "runs", stamp)
    existing_manifest = None
    manifest_path = os.path.join(run_dir, "manifest.json")
    if not args.merge_run and args.out and os.path.isfile(manifest_path):
        # 往已有 run 目录里增量重跑必须走 --merge-run。少了这个门槛，下面 else 分支
        # 会用"本轮任务集"重写 manifest（tasks 列表与指纹都缩到本轮范围），
        # 一整行的题集就只剩最后一次复跑的那几题——2026-09-21 真实踩过。
        print(f"--out {args.out} 已是一个已完成的 run；增量复跑请加 --merge-run"
              f"（它会保留原 run 的题集与指纹，并把被复跑的旧尝试归档到 .attempts/）", file=sys.stderr)
        return 2
    if args.merge_run:
        if not args.out or not os.path.isfile(manifest_path):
            print("--merge-run requires an existing --out run directory", file=sys.stderr)
            return 1
        existing_manifest = _load_yaml(manifest_path)
        allowed_tasks = set(existing_manifest.get("tasks", []))
        invalid_tasks = [task.id for task in tasks if task.id not in allowed_tasks]
        if invalid_tasks:
            print("merge tasks are not part of the original run: " + ",".join(invalid_tasks), file=sys.stderr)
            return 1
        if set(provider_configs) != set(existing_manifest.get("models", [])):
            print("merge model label must match the original run", file=sys.stderr)
            return 1
    os.makedirs(run_dir, exist_ok=True)
    task_fingerprints = {
        task.id: task_fingerprint(task, ROOT)
        for task in tasks
    }
    suite_fingerprint = hashlib.sha256(
        "\n".join(f"{task_id}:{digest}" for task_id, digest in sorted(task_fingerprints.items())).encode()
    ).hexdigest()[:16]
    model_runtime = {
        name: {**{key: config.get(key) for key in (
            "base_url", "upstream_url", "model", "wire_api", "reasoning_effort",
            "timeout_s", "max_output_tokens"
        )},
            # 外部端点必须自己声明出来：一行云端跑分和一行的本地量化跑分不同尺，
            # 混在同一张榜上不标来源，就是拿"哪家 API 好用"冒充"模型能力"。
            # 只记来源类型与环境变量名，密钥本身永不进 manifest。
            "endpoint": _endpoint_kind(_provenance_url(config)),
            "key_source": config.get("_key_source"),
        }
        for name, config in provider_configs.items()
    }
    if existing_manifest is not None:
        manifest = dict(existing_manifest)
        fingerprints = dict(manifest.get("task_fingerprints", {}))
        fingerprints.update(task_fingerprints)
        suite_fingerprint = hashlib.sha256(
            "\n".join(f"{task_id}:{digest}" for task_id, digest in sorted(fingerprints.items())).encode()
        ).hexdigest()[:16]
        runtime = dict(manifest.get("model_runtime", {}))
        runtime.update(model_runtime)
        manifest.update({
            "model_runtime": runtime,
            "jobs": args.jobs,
            "task_fingerprints": fingerprints,
            "suite_fingerprint": suite_fingerprint,
            "judge": (parse_judge_choice(args.judge) is not None) or bool(manifest.get("judge")),
            "last_resumed_at": stamp,
            "resume_count": int(manifest.get("resume_count", 0)) + 1,
        })
    else:
        manifest = {"started_at": stamp, "models": list(provider_configs), "model_runtime": model_runtime,
                    "tasks": [t.id for t in tasks],
                    "jobs": args.jobs, "judge": judge_cfg is not None,
                    "task_fingerprints": task_fingerprints, "suite_fingerprint": suite_fingerprint}
    # 裁判身份进 manifest：分数只有"同一把尺"之间才可比。
    rubric_tasks = [t.id for t in tasks if t.rubric]
    judge_profile = judge_identity(judge_cfg) if judge_cfg else None
    manifest["judge_profile"] = judge_profile
    if judge_cfg:
        print(f"[judge] 本轮尺子：{judge_profile}（不点名就是 models.yaml 的 judge.default；"
              f"--no-judge 可关）", flush=True)
    if rubric_tasks and judge_cfg is None:
        print(f"[warn] --no-judge：{len(rubric_tasks)} 道带 rubric 的题只跑确定性半边，"
              f"判定记\"未评\"、不进通过率分母：{', '.join(rubric_tasks[:6])}"
              f"{'…' if len(rubric_tasks) > 6 else ''}", flush=True)
    mismatched = [t.id for t in tasks if t.rubric and t.judge_id
                  and judge_cfg is not None and t.judge_id != judge_profile]
    if mismatched:
        # 维护者口径（2026-11-03）：尺不重要，judge_id 只是题面里的历史参考，不改判分流程。
        print(f"[info] 题面参考的裁判档案与本轮不同，仍按本轮尺子判分："
              f"{', '.join(mismatched)}（本轮 {judge_profile}）", flush=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if existing_manifest is not None:
        attempt_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        for task in tasks:
            for model_name in provider_configs:
                current = os.path.join(run_dir, model_name, task.id)
                if not os.path.isdir(current):
                    continue
                archived = os.path.join(run_dir, ".attempts", model_name, task.id, attempt_stamp)
                os.makedirs(os.path.dirname(archived), exist_ok=True)
                shutil.move(current, archived)
                print(f"[retry] archived {model_name} :: {task.id} -> {os.path.relpath(archived, run_dir)}", flush=True)

    jobs = [(task, mname) for task in tasks for mname in provider_configs]
    print_lock = Lock()
    pause_gate = Event()
    pause_gate.set()
    if hasattr(signal, "SIGUSR1") and hasattr(signal, "SIGUSR2"):
        signal.signal(signal.SIGUSR1, lambda _signum, _frame: pause_gate.clear())
        signal.signal(signal.SIGUSR2, lambda _signum, _frame: pause_gate.set())
        print("[control] ready pause=SIGUSR1 resume=SIGUSR2", flush=True)

    def execute(index, task, mname):
        pause_gate.wait()
        with print_lock:
            print(f"\n[run {index}/{len(jobs)}] {mname} :: {task.id}  ({task.difficulty})", flush=True)
        provider = ModelProvider(provider_configs[mname])
        # 实时思考流只在 Web 控制台发车时开：命令行直接跑时这些行只会把终端刷满。
        sink = EventSink(task.id, mname) if getattr(args, "events", False) else None
        if sink is not None:
            provider.on_event = sink
        # 每道题一个裁判实例：provider 的重试计数是实例状态，共用会串题。
        local_judge = Judge(judge_cfg) if judge_cfg is not None else None
        try:
            result = run_task(task, provider, os.path.join(run_dir, mname),
                              base=os.path.join(ROOT, "environments"), judge=local_judge,
                              pause_gate=pause_gate)
        finally:
            if sink is not None:
                # 收尾把缓冲里剩的字吐完，否则每道题最后几十个字会留在内存里丢掉。
                sink.flush()
        pause_gate.wait()
        mark = "未评" if not result.graded else ("PASS" if result.passed else "FAIL")
        with print_lock:
            print(f"[end {index}/{len(jobs)}] {mname} :: {task.id}  {mark}  "
                  f"steps={result.efficiency.steps} tokens={result.efficiency.tokens} "
                  f"t={result.efficiency.elapsed_s}s  {result.failure_reason or ''}", flush=True)
        return mname, task.id, result

    results = []
    # 串行路径也走同一把闸：这样 jobs=1 开跑后，看板照样能把并发拉到 2..4，
    # 不必为了"想加人"而重开一轮（重开就意味着丢掉已完成的题目吗？不会，但白烧钱）。
    # ceiling 必须是本轮端点真正吃下的那个数（本地 4 / 外部 16），不然 control.json 会
    # 对外宣告"可以调到 16"，而 --jobs 的开跑校验只允许到 4——同一个数字两处说法不一。
    with GatedPool(jobs=args.jobs, control_path=os.path.join(run_dir, "control.json"),
                   print_lock=print_lock, ceiling=max_jobs) as pool:
        futures = [pool.submit(execute, index, task, mname)
                   for index, (task, mname) in enumerate(jobs, 1)]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: (item[0], item[1]))
    final_results = _load_run_results(run_dir) if existing_manifest is not None else results
    expected_results = len(manifest.get("tasks", [])) * len(manifest.get("models", []))
    if len(final_results) == expected_results:
        _append_history(run_dir, final_results)
    else:
        print(f"[history] 尚未入库：当前 {len(final_results)}/{expected_results} 个任务结果", flush=True)
    print(f"\n[done] run dir: {run_dir}")
    return 0


def cmd_report(args):
    run_dir = args.run
    mf = os.path.join(run_dir, "manifest.json")
    manifest = _load_yaml(mf) if os.path.exists(mf) else {"models": [], "tasks": []}
    results = []
    for yf in glob.glob(os.path.join(run_dir, "**", "result.json"), recursive=True):
        model = os.path.basename(os.path.dirname(os.path.dirname(yf)))
        results.append((model, _load_yaml(yf)))
    if not results:
        print("no results found in", run_dir)
        return 1
    by_model = {}
    for model, r in results:
        by_model.setdefault(model, []).append(r)

    dims = sorted({d for _, r in results for d in r.get("scores", {})})
    ungraded_all = sum(1 for _, r in results if not r.get("graded", True))
    lines = ["# 评测报告", "", f"- run: `{run_dir}`", f"- models: {', '.join(manifest['models'])}",
             f"- tasks: {len(results)}（其中未评 {ungraded_all}：上游故障或 rubric 没评成，不计入通过率分母）",
             f"- judge: {manifest.get('judge_profile') or '本轮未启用'}"]
    for name in manifest["models"]:
        rt = (manifest.get("model_runtime") or {}).get(name) or {}
        if rt.get("endpoint") == "external":
            lines.append(f"- ⚠ 被测端点是**外部 API**：{name} → {rt.get('base_url')}"
                         f"（wire={rt.get('wire_api')}，密钥来源 {rt.get('key_source') or 'models.yaml'}）。"
                         "这一行与本地量化端点不同尺，横向排序时按来源分层看。")
    lines += ["- accounted_tok/s = 累计输入+输出 token / 任务墙钟时间；用于本评测内相对比较，不等同于纯生成速度。",
             "", "## 总分、速度与消耗", "",
             "| model | passed | graded | ungraded | rate | avg_steps | avg_tokens | avg_elapsed_s | accounted_tok/s |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for model in sorted(by_model):
        rs = by_model[model]
        graded = [x for x in rs if x.get("graded", True)]
        stats = graded or rs          # 未评的尝试是残缺轨迹，不该污染步数/耗时口径
        p = sum(x["passed"] for x in graded)
        total_tokens = sum(x["efficiency"]["tokens"] for x in stats)
        total_elapsed = sum(x["efficiency"]["elapsed_s"] for x in stats)
        rate = (p / len(graded)) if graded else 0.0
        lines.append(f"| {model} | {p} | {len(graded)} | {len(rs) - len(graded)} | {rate:.0%} | "
                     f"{sum(x['efficiency']['steps'] for x in stats) / len(stats):.1f} | "
                     f"{total_tokens / len(stats):.0f} | {total_elapsed / len(stats):.2f} | "
                     f"{total_tokens / total_elapsed if total_elapsed else 0:.2f} |")

    lines += ["", "## 维度得分（各维度平均）", "", "| model | " + " | ".join(dims) + " |",
              "|---|" + "---|" * len(dims)]
    for model in sorted(by_model):
        cells = []
        for d in dims:
            vals = [r["scores"][d] for r in by_model[model] if d in r.get("scores", {})]
            cells.append(f"{sum(vals) / len(vals):.2f}" if vals else "-")
        lines.append(f"| {model} | " + " | ".join(cells) + " |")

    lines += ["", "## 任务明细", "",
              "| model | task | passed | steps | tokens | elapsed_s | accounted_tok/s | judge | failure |",
              "|---|---|---|---:|---:|---:|---:|---|---|"]
    for model, r in sorted(results, key=lambda x: (x[0], x[1]["task_id"])):
        js = f"{r['judge']['score']:.2f}" if r.get("judge") else "-"
        elapsed = r["efficiency"]["elapsed_s"]
        throughput = r["efficiency"]["tokens"] / elapsed if elapsed else 0
        # Y=通过 N=失败 U=未评（上游故障或 rubric 没评成）。U 不是能力判定，
        # 读报告和读网页时必须跟 N 分开看。
        mark = "Y" if r["passed"] else ("U" if not r.get("graded", True) else "N")
        lines.append(f"| {model} | {r['task_id']} | {mark} | "
                     f"{r['efficiency']['steps']} | {r['efficiency']['tokens']} | {elapsed:.2f} | "
                     f"{throughput:.2f} | {js} | {r.get('failure_reason') or ''} |")
    md = "\n".join(lines)
    print(md)
    with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    return 0


def cmd_rejudge(args):
    """重判：拿已归档的轨迹只重跑裁判闸门，不重新采样被测模型。

    一道带 rubric 的题记「未评」绝大多数是裁判那头掉了，不是模型的问题。这种 attempt
    重新采样会把 token 再烧一遍、还得到一个不一样的轨迹；缺的只是判决，那就只补判决。
    """
    load_env_file(os.path.join(ROOT, ".env"))
    if not 1 <= args.jobs <= 8:
        print("--jobs 重判时只允许 1..8：裁判是外部端点，再高只是排队", file=sys.stderr)
        return 2
    run_dir = os.path.abspath(args.out)
    manifest_path = os.path.join(run_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        print(f"重判需要一个已有 run 目录（缺 manifest.json）：{run_dir}", file=sys.stderr)
        return 2
    manifest = _load_yaml(manifest_path)
    data = _load_yaml(os.path.join(ROOT, "models.yaml"))
    try:
        # 重判的本体就是判决，没有"关掉裁判"这一说：只有选哪一把尺。
        judge_cfg = resolve_judge_profile(data, parse_judge_choice(getattr(args, 'judge', None)))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    key_ready, key_from = judge_key_source(judge_cfg)
    if not key_ready:
        var = key_from[4:] if key_from.startswith("env:") else key_from
        print(f"重判要调用裁判端点，但档案 {judge_cfg['name']} 的密钥不可用（{key_from}）："
              f"把 {var} 写进仓库根的 .env 或先 export", file=sys.stderr)
        return 2
    identity = judge_identity(judge_cfg)

    wanted_models = ([x.strip() for x in args.models.split(",") if x.strip()]
                     if args.models else list(manifest.get("models", [])))
    if not wanted_models:
        print("manifest 里没有模型行，重判无事可做", file=sys.stderr)
        return 2
    tasks_by_id = {t.id: t for t in discover_tasks(os.path.join(ROOT, "tasks"))}
    if args.tasks:
        picked = [x.strip() for x in args.tasks.split(",") if x.strip()]
        unknown = [x for x in picked if x not in tasks_by_id]
        if unknown:
            print("题目不存在：" + ",".join(unknown), file=sys.stderr)
            return 2
        task_filter = picked
    else:
        task_filter = [x for x in manifest.get("tasks", []) if x in tasks_by_id]

    attempts = [a for a in list_attempts(run_dir, wanted_models, task_filter,
                                         only_ungraded=not args.include_graded)
                if a["previous"].get("task_id") in tasks_by_id]
    # 采样本身就没跑成的 attempt（上游 401 / 连不上）缺的是轨迹不是判决，只有重跑能救。
    unsamplable = [a for a in attempts if not a["rejudgeable"]]
    attempts = [a for a in attempts if a["rejudgeable"]]
    # 题面钉了别的裁判档案 = 另一把尺，重判出来的 verdict 不进同一张榜，直接挡掉。
    def pinned_elsewhere(attempt):
        declared = tasks_by_id[attempt["task_id"]].judge_id
        return bool(declared) and declared != identity

    # 维护者口径（2026-11-03）：尺不重要。题面 judge_id 只作参考，不再挡重判；
    # 要回到严格模式（必须同尺才判）设 EVALKIT_JUDGE_PIN=1。
    wrong_ruler = []
    if judge_any_allowed():
        pinned = sum(1 for a in attempts if pinned_elsewhere(a))
        if pinned:
            print(f"[rejudge] 尺不重要：{pinned} 道题的题面参考了别的裁判档案，"
                  f"本轮一并按 {identity} 判", flush=True)
    else:
        wrong_ruler = [a for a in attempts if pinned_elsewhere(a)]
        attempts = [a for a in attempts if not pinned_elsewhere(a)]
    no_rubric = [a for a in attempts if not tasks_by_id[a["task_id"]].rubric]
    attempts = [a for a in attempts if tasks_by_id[a["task_id"]].rubric]
    # attempt 存的指纹与当前尺不一致时先别急着判：那说明题面或判分代码动过，
    # 全榜要先走一次指纹重对齐，否则重判出来的分数会和同 run 其它题不同尺。
    stored = manifest.get("task_fingerprints") or {}

    def stale(attempt):
        before = stored.get(attempt["task_id"])
        return bool(before) and before != task_fingerprint(tasks_by_id[attempt["task_id"]], ROOT)

    if getattr(args, "force_fingerprint", False):
        # 维护者定的口径：裁判智力够就判得对，指纹漂移不拦（题面措辞小改不影响这些闸门）。
        print("[rejudge] --force-fingerprint：跳过指纹对齐检查，直接按当前题面判", flush=True)
        out_of_scale = []
    else:
        out_of_scale = [a for a in attempts if stale(a)]
        attempts = [a for a in attempts if not stale(a)]

    print(f"[rejudge] run={os.path.basename(run_dir)} judge={identity} "
          f"候选={len(attempts)} 跳过(未采样成)={len(unsamplable)} "
          f"跳过(裁判档案不符)={len(wrong_ruler)} 跳过(无 rubric)={len(no_rubric)} "
          f"跳过(指纹未对齐)={len(out_of_scale)}", flush=True)
    for group, why in ((unsamplable, "采样未跑成，需重跑"),
                       (wrong_ruler, "题面钉的裁判档案与本轮不符"),
                       (no_rubric, "无 rubric，判决本来就齐"),
                       (out_of_scale, "attempt 指纹与当前判分口径不一致，先重对齐指纹")):
        for a in group:
            detail = (f" — 题面钉 {tasks_by_id[a['task_id']].judge_id}，实跑 {identity}"
                      if group is wrong_ruler else "")
            print(f"  skip {a['model']} :: {a['task_id']} — {why}{detail}", flush=True)
    for a in attempts:
        print(f"  plan {a['model']} :: {a['task_id']}  "
              f"{a['previous'].get('ungraded_reason') or ''}"[:180], flush=True)
    if args.dry_run or not attempts:
        return 0

    print_lock = Lock()
    counters = {"changed": 0, "still_ungraded": 0, "failed": 0}

    def work(index, attempt):
        # 每个 attempt 起一个 Judge：provider 的 retry_stats 是实例状态，
        # 共用一个裁判实例会把三次尝试的重试计数串到别的题上。
        judge = Judge(judge_cfg)
        outcome = rejudge_attempt(tasks_by_id[attempt["task_id"]], attempt, judge)
        with print_lock:
            if not outcome["ok"]:
                counters["failed"] += 1
                print(f"[fail {index}/{len(attempts)}] {outcome['model']} :: {outcome['task_id']}  "
                      f"{outcome['error'][:200]}", flush=True)
            else:
                if outcome["changed"]:
                    counters["changed"] += 1
                if not outcome["graded"]:
                    counters["still_ungraded"] += 1
                flag = "未评" if not outcome["graded"] else ("PASS" if outcome["passed"] else "FAIL")
                # 行格式与 run 的 [end i/n] 一样是给看板读的：重判不改采样事实，所以这行
                # 不带 steps/tokens，进度只按"补上了一个结论"算。
                print(f"[done {index}/{len(attempts)}] {outcome['model']} :: {outcome['task_id']}  "
                      f"{outcome['before']} -> {flag}  judge={outcome['judge_score']}  "
                      f"{outcome['reason'][:120]}", flush=True)
        return outcome

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(lambda item: work(*item), enumerate(attempts, 1)))

    # 判完把历史那一行按新的 result 口径重算：不然看板读到的还是旧分数。
    final_results = _load_run_results(run_dir)
    if final_results:
        _append_history(run_dir, final_results)
    print(f"[rejudge] 结论变化 {counters['changed']} / 仍未评 {counters['still_ungraded']} "
          f"/ 失败 {counters['failed']}（旧判决已归档到 .attempts/）", flush=True)
    return 0


def cmd_history(args):
    """读取 runs/_HISTORY.jsonl，输出跨 run 的对比表。"""
    hist = os.path.join(ROOT, "runs", "_HISTORY.jsonl")
    if not os.path.exists(hist):
        print("暂无历史记录（先跑一次 run）")
        return 1
    entries = [json.loads(l) for l in open(hist, encoding="utf-8") if l.strip()]
    if not entries:
        print("暂无历史记录")
        return 1
    lines = ["# 测试历史（跨 run 对比）", "",
             "| run | 时间 | model | pass(graded) | 未评 | rate | avg_steps | avg_tokens | 维度均分 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for e in entries:
        for mname, m in sorted(e["models"].items()):
            if args.model and mname != args.model:
                continue
            dims = " ".join(f"{d}:{v:.2f}" for d, v in sorted(m["dims"].items()) if d != "judge")
            lines.append(f"| {e['run_id']} | {e['timestamp']} | {mname} | "
                         f"{m['passed']}/{m['total']} | {m.get('ungraded', 0)} | {m['rate']:.0%} | {m['avg_steps']} | "
                         f"{m['avg_tokens']} | {dims} |")
    print("\n".join(lines))
    return 0


def cmd_banks(args):
    """列出可用题库。"""
    banks = list_banks()
    if not banks:
        print("(没有题库；用 tools/build_bank.py 生成)")
        return 0
    for name, b in sorted(banks.items()):
        n = len(b.get("tasks") or [])
        print(f"{name:16s} {n:3d} 题  {b.get('description','')}")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="agent-eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="跑评测")
    r.add_argument("--tasks", help="任务过滤：id 或 category，逗号分隔；默认全部")
    r.add_argument("--bank", metavar="NAME", default="hard40",
                   help="用 banks/ 里的命名题库选题（默认 hard40，40 题）；与 --tasks 同时给出时取交集；"
                        "传空串 --bank '' 则不限题库")
    r.add_argument("--models", help="被测模型名，逗号分隔；默认 all")
    r.add_argument("--model-label", help="单模型运行的结果名称，用于目录、历史和排行榜")
    r.add_argument("--api-model", help="覆盖请求中的 OpenAI API model 字段（单模型运行）")
    r.add_argument("--base-url", help="覆盖连接端点，例如 https://api.example.com/v1（外部 API 用）")
    r.add_argument("--wire-api", choices=("chat", "responses"),
                   help="覆盖线路协议：chat=/chat/completions，responses=/responses")
    r.add_argument("--api-key", help="直接给 Authorization: Bearer 用的密钥。"
                                     "会出现在 ps 与 shell 历史里，能用 --api-key-env 就别用它")
    r.add_argument("--api-key-env", metavar="VAR",
                   help="从该环境变量名读取密钥（推荐：密钥不落 argv、不落 models.yaml、不落 manifest）")
    r.add_argument("--max-output-tokens", type=int,
                   help="覆盖单次模型响应最大 token（256..262144）")
    r.add_argument("--request-timeout-s", type=int,
                   help="覆盖单次模型请求超时秒数（10..3600）")
    r.add_argument("--out", help="run 输出目录")
    r.add_argument("--jobs", type=int, default=1, metavar="N",
                   help="并发任务数：本地端点 1..4，外部 API 端点 1..16（默认 1）")
    # 默认就是开的：不带 --judge 也用裁判，尺子是 models.yaml 里 judge.default 那一档。
    # 想要另一把尺就报档名；不想要裁判用 --no-judge（后者是同一个 dest 上的 store_const，
    # argparse 按出现顺序后者覆盖前者）。
    r.add_argument("--judge", nargs="?", const=JUDGE_DEFAULT, default=JUDGE_DEFAULT,
                   metavar="PROFILE",
                   help="启用 LLM 裁判打分（对带 rubric 的任务生效）；省略 PROFILE 就用 "
                        "models.yaml 的 judge.default，写了就用那个档案（如 luna-6）")
    r.add_argument("--no-judge", dest="judge", action="store_const", const=None,
                   help="关掉 LLM 裁判：带 rubric 的题只跑确定性半边，判定记\"未评\"")
    r.add_argument("--events", action="store_true",
                   help="把每个任务的思考流/工具调用按 [ev] 行打到 stdout（Web 控制台用）")
    r.add_argument("--skip-preflight", action="store_true",
                   help="跳过开跑前的上游连通性检查（不推荐：不可达的尝试只会被记成未评）")
    r.add_argument("--preflight-wait-s", type=int, default=120,
                   help="开跑前最多等上游多久（默认 120 秒，模型装载期就吃在这里）")
    r.add_argument("--max-retries", type=int, default=4,
                   help="单次模型请求因上游故障可追加的重试次数（默认 4，指数退避）")
    r.add_argument("--merge-run", action="store_true",
                   help="增量写回已有 --out run；仅执行 --tasks 指定题并归档旧尝试")
    r.set_defaults(fn=cmd_run)
    bk = sub.add_parser("banks", help="列出可用题库（跑评测时用 --bank NAME 选择）")
    bk.set_defaults(fn=cmd_banks)
    p = sub.add_parser("report", help="生成报告")
    p.add_argument("--run", required=True, help="run 目录")
    p.set_defaults(fn=cmd_report)
    rj = sub.add_parser("rejudge", help="只重跑裁判闸门：不重新采样，补成因裁判故障缺失的判定")
    rj.add_argument("--out", required=True, help="已有 run 目录")
    rj.add_argument("--tasks", help="题 id 逗号分隔；默认该 run 里所有未评的 attempt")
    rj.add_argument("--models", help="只重判这些模型行；默认 run manifest 里的模型")
    rj.add_argument("--include-graded", action="store_true",
                    help="连已有结论的 attempt 一起重判（重审）；默认只动未评的")
    rj.add_argument("--jobs", type=int, default=2, metavar="N",
                    help="并发重判几道题（裁判是外部端点，默认 2）")
    rj.add_argument("--judge", nargs="?", const=JUDGE_DEFAULT, default=JUDGE_DEFAULT,
                    metavar="PROFILE",
                    help="重判用哪一把尺（省略则用 models.yaml 的 judge.default）。"
                         "要补历史那几行的结论就得点名它钉的那把（如 luna-6）")
    rj.add_argument("--force-fingerprint", action="store_true",
                    help="不按任务指纹拦重判（题面/判分口径小改动时由人确认放行）")
    rj.add_argument("--force-ruler", action="store_true",
                    help="连题面钉了别的裁判档案的 attempt 也用本轮这把尺判（默认挡掉；"
                         "判出来的分与钉尺的历史行不同尺，日志会标）")
    rj.add_argument("--dry-run", action="store_true", help="只列出会重判哪些 attempt，不写盘")
    rj.set_defaults(fn=cmd_rejudge)
    h = sub.add_parser("history", help="查看跨 run 测试历史对比")
    h.add_argument("--model", help="只看某模型")
    h.set_defaults(fn=cmd_history)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
