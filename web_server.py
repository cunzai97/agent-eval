#!/usr/bin/env python3
"""Local Tracebench web server with a constrained evaluation runner API."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

import yaml

from evalkit.schema import TaskSpec
from evalkit.fingerprint import task_fingerprint
# _FRAMEWORK 是"哪些文件的内容会改变判分口径"的唯一真相，这里只借来做变更探测：
# 不去 evalkit/fingerprint.py 里加公开别名，因为那个文件本身就在被哈希的清单里，
# 动它一个字节就会让全部 50 道题的指纹变化、整张榜重新变成 stale。
from evalkit.fingerprint import _FRAMEWORK as FRAMEWORK_INPUTS
from evalkit.envfile import load_env_file
from evalkit.judge import JUDGE_DEFAULT, judge_identity, judge_profiles, parse_judge_choice
from evalkit.rejudge import list_attempts, reason_needs_resample


ROOT = Path(__file__).resolve().parent
# 发车前的密钥来源：models.yaml 里只存 ${VAR} 引用，值放仓库根的 .env（.gitignore 已盖）。
# 服务端自己也读一遍，才能在外部队点缺密钥时**开跑前**就拦住，而不是让 50 道题一起 401。
load_env_file(ROOT / ".env")
WEB_ROOT = ROOT / "web"


def load_banks():
    """banks/*.yaml → [{name, description, tasks}]；题库只是题号清单，tasks/ 不动。"""
    banks = []
    for p in sorted((ROOT / "banks").glob("*.yaml")):
        try:
            spec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        ids = [str(x).strip() for x in (spec.get("tasks") or []) if str(x).strip()]
        if not ids:
            continue
        banks.append({"name": str(spec.get("name") or p.stem),
                      "description": str(spec.get("description") or ""),
                      "tasks": ids})
    return banks
ACTIVE_STATUSES = {"starting", "running", "pausing", "paused", "cancelling", "finalizing"}
# 看板的持久状态。放在 runs/ 而不是 web/：web/ 是被 _serve_static 直接对外提供的
# 目录，把可变状态写进去等于把它变成一个可被任何人 GET 的公开文件。
BOARD_PATH = ROOT / "runs" / "_BOARD.json"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
LOCAL_MAX_JOBS, EXTERNAL_MAX_JOBS = 4, 16


def max_jobs_for(endpoint: str) -> int:
    """端点性质决定并发天花板：本地单推理槽再高只是排队，外部配额在服务端。"""
    host = (urlsplit(str(endpoint or ""))).hostname or ""
    return LOCAL_MAX_JOBS if host in LOCAL_HOSTS else EXTERNAL_MAX_JOBS


def endpoint_kind(endpoint: str) -> str:
    """local = 回环地址上的自建推理服务；external = 任何别的主机。"""
    host = (urlsplit(str(endpoint or ""))).hostname or ""
    if host in LOCAL_HOSTS or host.startswith("127."):
        return "local"
    return "external"


def classify_target(profile: dict, override: str = "") -> str:
    """并发天花板与来源分层按**真实上游**算，不按转发器。

    profile 可以是 models.yaml 里那条原始配置，也可以是 /api/config 里的连接配置
    （两种键名都认）。指向本机亲和前置（upstream_url，见
    （内部缓存专项报告，未随仓库发布））只是换一条出口，那一行仍然是云端
    API：照回环地址算会把外部跑分压到本地那 4 个 worker，还会让榜单把付费 API
    标成本地量化。面板临时填的地址（override）本身就是真实上游。
    """
    return (override or profile.get("upstreamUrl") or profile.get("upstream_url")
            or profile.get("endpoint") or profile.get("base_url") or "")


def profile_for_runtime(models: list[dict], runtime: dict) -> dict | None:
    """按 manifest 记的端点找回连接配置。

    同一条 run 的地址可能记成真实上游（接入本机转发器之前），也可能记成转发器 ——
    两个都算匹配，否则给 luna 那几行加一行 upstream_url 就把历史 run 的续跑卡死了。
    """
    recorded = {runtime.get("base_url"), runtime.get("upstream_url")} - {None, ""}
    for item in models:
        if recorded & ({item.get("endpoint"), item.get("upstreamUrl")} - {None, ""}):
            return item
    return None


# 面板里现填的密钥走这个变量名注入子进程环境：不进 argv（ps 能看到）、不进日志、不落盘。
WEB_KEY_ENV = "TRACEBENCH_API_KEY"


def key_state(api_key_value: str) -> tuple[bool, str]:
    """models.yaml 里这条密钥此刻能不能用：返回 (可用?, 来源标签)。

    只看 ``${VAR}`` 引用的变量在不在，**不碰值**、也不把值往外带——这个标签会跟着
    连接配置一起去浏览器，密钥本身没理由跟着走。
    """
    text = str(api_key_value or "")
    if text.startswith("${") and text.endswith("}"):
        name = text[2:-1]
        return bool(os.environ.get(name)), f"env:{name}"
    text = text.strip()
    return bool(text), "连接配置内联"


def load_judge_catalog() -> tuple[dict[str, dict], str | None]:
    """读 models.yaml 的裁判档案表 + 默认档名。

    只规范化结构，**不展开密钥**：``${VAR}`` 原样留着，密钥就绪与否由 :func:`key_state`
    当场看环境变量——这份清单会进面板，值不能出口。
    """
    data = yaml.safe_load((ROOT / "models.yaml").read_text(encoding="utf-8"))
    return judge_profiles(data)


def judge_view(name: str, profile: dict, default_name: str | None) -> dict:
    """面板里一行裁判选项：只有名字/模型/端点与"缺不缺密钥"，没有密钥本身。"""
    ready, source = key_state(str(profile.get("api_key", "")))
    return {
        "name": name,
        "apiModel": profile.get("model", ""),
        "endpoint": profile.get("base_url", ""),
        "wireApi": profile.get("wire_api", "chat"),
        "isDefault": name == default_name,
        "keyReady": ready,
        "keySource": source,
    }


def display_path(path: Path) -> str:
    """给日志和接口看的短路径。run 目录正常都在仓库里，但"必须如此"不该变成一个
    未捕获的 ValueError——那样一次越界写入会把已经落盘的设定报成失败。"""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def load_catalog() -> tuple[list[dict], list[dict]]:
    models_data = yaml.safe_load((ROOT / "models.yaml").read_text(encoding="utf-8"))
    models = [
        {
            "name": model["name"],
            "endpoint": model.get("base_url", ""),
            "upstreamUrl": model.get("upstream_url", ""),
            "apiModel": model.get("model", "local"),
            "timeoutSeconds": model.get("timeout_s", 900),
            "maxOutputTokens": model.get("max_output_tokens"),
            "maxJobs": max_jobs_for(classify_target(model)),
            "kind": endpoint_kind(classify_target(model)),
            "wireApi": model.get("wire_api", "chat"),
            # 密钥只给"能不能用"和"从哪来"，值不出口：外部端点缺密钥时面板要先能看出来。
            "keyReady": key_state(model.get("api_key", ""))[0],
            "keySource": key_state(model.get("api_key", ""))[1],
        }
        for model in models_data.get("models", [])
    ]
    tasks = []
    for path in sorted((ROOT / "tasks").rglob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        fingerprint = task_fingerprint(TaskSpec(**data), ROOT)
        tasks.append({
            "id": data["id"],
            "title": data["title"],
            "category": data["category"],
            "difficulty": data.get("difficulty", "unknown"),
            "budget": data.get("budget", {}),
            "fingerprint": fingerprint,
        })
    return models, tasks


def catalog_signature() -> tuple:
    """题面 yaml、模型表和判分框架的 mtime 元组——变了就说明"当前尺"变了。

    只 stat 不读内容，所以每次请求都能跑；真变了才去 load_catalog() 重算指纹。
    """
    paths = sorted((ROOT / "tasks").rglob("*.yaml")) + [ROOT / "models.yaml"]
    paths += [ROOT / relative for relative in FRAMEWORK_INPUTS]
    return tuple((path.stat().st_mtime_ns if path.exists() else -1) for path in paths)


class BoardStore:
    """任务看板的持久状态：泳道意图、手动顺序、难度覆盖、并发设定。

    这里**只存意图，不存事实**。哪个任务正在跑、哪题判了什么，永远从 RunManager 的
    实时状态和 runs/ 里的结果推导；板子上记的只有"用户把它拖到哪一列、按什么排"。
    这么分是因为事实会自己变（跑完、被判分），而把事实抄一份到板子上就会有两份真相
    ——那是最难查的一类 bug。
    """

    LANES = ("queue", "running", "done")
    SORTS = ("manual", "difficulty_desc", "difficulty_asc", "measured_desc", "measured_asc")
    DIFFICULTY = ("easy", "medium", "hard")

    def __init__(self):
        self.lock = threading.RLock()

    def load(self) -> dict:
        try:
            data = json.loads(BOARD_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        return self._normalize(data)

    def _normalize(self, data: dict) -> dict:
        board = {
            "order": [str(x) for x in (data.get("order") or []) if isinstance(x, str)],
            "queued": [str(x) for x in (data.get("queued") or []) if isinstance(x, str)],
            "difficulty": {str(k): v for k, v in (data.get("difficulty") or {}).items()
                           if v in self.DIFFICULTY},
            "sort": data.get("sort") if data.get("sort") in self.SORTS else "manual",
            "jobs": data.get("jobs") if isinstance(data.get("jobs"), int) else None,
            "updatedAt": data.get("updatedAt"),
        }
        return board

    def save(self, board: dict) -> None:
        BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = BOARD_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, BOARD_PATH)

    def patch(self, request: dict, known_ids: set, ceiling: int) -> dict:
        """局部更新看板。只接受白名单字段，非法值整条拒绝而不是静默改。"""
        with self.lock:
            board = self.load()
            if "order" in request:
                order = [str(x) for x in (request.get("order") or [])]
                if any(x not in known_ids for x in order):
                    raise ValueError("顺序里含未知任务")
                if len(set(order)) != len(order):
                    raise ValueError("顺序里有重复任务")
                # 没点名的任务按现有顺序补在尾部：新增题目不该因为没进过板子就消失
                board["order"] = order + [t for t in board["order"] if t not in set(order)]
            if "queued" in request:
                queued = [str(x) for x in (request.get("queued") or [])]
                if any(x not in known_ids for x in queued):
                    raise ValueError("排队里含未知任务")
                if len(set(queued)) != len(queued):
                    raise ValueError("排队里有重复任务")
                board["queued"] = queued
            if "toggleQueued" in request:
                task_id = str(request.get("toggleQueued"))
                if task_id not in known_ids:
                    raise ValueError("未知任务")
                board["queued"] = ([t for t in board["queued"] if t != task_id]
                                   if task_id in board["queued"] else board["queued"] + [task_id])
            if "difficulty" in request:
                for task_id, value in (request.get("difficulty") or {}).items():
                    if task_id not in known_ids:
                        raise ValueError(f"难度覆盖里含未知任务 {task_id}")
                    if value not in self.DIFFICULTY:
                        raise ValueError("难度只能是 easy/medium/hard")
                    board["difficulty"][task_id] = value
            if "clearDifficulty" in request:
                task_id = str(request.get("clearDifficulty"))
                board["difficulty"].pop(task_id, None)
            if "sort" in request:
                if request.get("sort") not in self.SORTS:
                    raise ValueError("未知排序方式")
                board["sort"] = request["sort"]
            if "jobs" in request:
                try:
                    jobs = int(request.get("jobs"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("并发数必须是整数") from exc
                if not 1 <= jobs <= max(1, ceiling):
                    raise ValueError(f"这个端点的并发只能是 1–{max(1, ceiling)}")
                board["jobs"] = jobs
            board["updatedAt"] = datetime.now().isoformat(timespec="seconds")
            self.save(board)
            return board


def measure_by_task(runs: list[dict]) -> dict:
    """按题汇总跨 run 的实测：通过率、被判过几次、平均 token/步数、最近一次结论。

    数据来源是 history_payload() 已经算好的每条 run 的 taskResults，其中带
    graded/stale 标记，所以这里沿用同一套分母口径：**未评与"题面已变更"的旧结果
    不进分母**。不这么做的后果很具体——把并发口径算错的两天里跑出来的行会一起进
    分母，把一道题"看起来很硬"。
    """
    stats: dict[str, dict] = {}
    for run in runs:
        stamp = run.get("timestamp") or ""
        for item in run.get("taskResults") or []:
            task_id = item.get("taskId")
            if not task_id:
                continue
            row = stats.setdefault(task_id, {
                "attempts": 0, "graded": 0, "passes": 0, "ungraded": 0, "stale": 0,
                "tokens": [], "steps": [], "passers": [], "models": set(),
                "last": None, "lastAt": "", "history": [],
            })
            row["attempts"] += 1
            # 分母只收"对当前题面成立的能力判定"。未评（上游故障/裁判没评成）和 stale
            # （题面已变更，那次结论说的是另一个版本的题）都留在分母之外：让 40 次旧版本
            # 的失败混进来，会把一道题从"4 次里 1 次通过"打成"44 次里 1 次通过"，
            # 实测难度排序整个就废了。两个排除项各自计数，缺了多少是看得见的。
            # 只有 tokens/步数照旧全收：成本量的是那次运行花了多少，与题面版本无关。
            if item.get("stale"):
                row["stale"] += 1
            elif item.get("graded", True) is False:
                row["ungraded"] += 1
            else:
                row["graded"] += 1
                if item.get("passed"):
                    row["passes"] += 1
                    row["passers"].append({"model": run.get("model"), "runId": run.get("runId")})
            if isinstance(item.get("tokens"), (int, float)):
                row["tokens"].append(item["tokens"])
            if isinstance(item.get("steps"), (int, float)):
                row["steps"].append(item["steps"])
            row["models"].add(run.get("model"))
            # 逐次尝试只留"谁、何时、结论、花了多少"，不留失败证据文本：一次 poll 要
            # 装 50 题 × N 次，把 evidence 塞进来会让 /api/board 胖到 500 KB，而点开
            # 卡片时本来就会按 run 单独取完整记录（那次才有必要带证据）。
            row["history"].append({
                "runId": run.get("runId"), "model": run.get("model"), "timestamp": stamp,
                "passed": bool(item.get("passed")), "graded": item.get("graded", True),
                "stale": bool(item.get("stale")), "steps": item.get("steps"),
                "tokens": item.get("tokens"), "elapsed": item.get("elapsed"),
            })
            if stamp >= row["lastAt"]:
                row["lastAt"], row["last"] = stamp, {
                    "model": run.get("model"), "runId": run.get("runId"), "timestamp": stamp,
                    "passed": bool(item.get("passed")), "graded": item.get("graded", True),
                    "stale": bool(item.get("stale")), "failure": item.get("failure"),
                    "tokens": item.get("tokens"), "steps": item.get("steps"),
                    "elapsed": item.get("elapsed"),
                }
    out = {}
    for task_id, row in stats.items():
        pass_rate = (row["passes"] / row["graded"]) if row["graded"] else None
        recent = sorted(row["history"], key=lambda item: item.get("timestamp") or "", reverse=True)
        out[task_id] = {
            "attempts": row["attempts"], "gradedAttempts": row["graded"], "passes": row["passes"],
            "ungradedAttempts": row["ungraded"], "staleAttempts": row["stale"],
            "passRate": None if pass_rate is None else round(pass_rate, 4),
            # 实测难度 = 1 - 通过率；没有样本时给 None，让前端显示"无数据"而不是"最简单"
            "measuredDifficulty": None if pass_rate is None else round(1 - pass_rate, 4),
            "avgTokens": round(sum(row["tokens"]) / len(row["tokens"])) if row["tokens"] else None,
            "avgSteps": round(sum(row["steps"]) / len(row["steps"]), 1) if row["steps"] else None,
            "modelCount": len(row["models"] - {None}),
            "passers": sorted({p["model"] for p in row["passers"] if p.get("model")}),
            "last": row["last"], "recent": recent[:12], "recentTotal": len(recent),
        }
    return out


class RunManager:
    def __init__(self) -> None:
        self.models, self.tasks = load_catalog()
        self.catalog_signature = catalog_signature()
        self.model_names = {model["name"] for model in self.models}
        self.task_ids = {task["id"] for task in self.tasks}
        self.categories = {task["category"] for task in self.tasks}
        # 裁判可选档案也缓存一份：面板要列出可选的尺子，发车前要先替它验密钥。
        self.judges, self.judge_default = load_judge_catalog()
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.cancel_requested = False
        self.logs: deque[dict] = deque(maxlen=2500)
        self.log_sequence = 0
        # 实时事件（思考流/工具调用/工具结果）：每个 run 重开一份。放在这里而不是 state
        # 里，是因为 state 会被整个 JSON 序列化发给浏览器，而这是高频追加的流。
        # 上限 8000 条：cli 已经把单条文本截到 400 字、观察截到 1200 字，最坏也就几 MB。
        self.events: deque[dict] = deque(maxlen=8000)
        self.event_sequence = 0
        self.state: dict = {
            "id": None,
            "status": "idle",
            "completed": 0,
            "passed": 0,
            "failed": 0,
            "total": 0,
            "startedAt": None,
            "endedAt": None,
            "elapsedSeconds": 0,
            "outputDir": None,
            "reportReady": False,
            "exitCode": None,
            "currentTasks": [],
            "taskRuns": [],
            "totalTokens": 0,
            "totalSteps": 0,
            "totalTaskSeconds": 0,
            "controlReady": False,
            "pausedAt": None,
            "pausedSeconds": 0,
        }

    def config(self) -> dict:
        category_counts: dict[str, int] = {}
        for task in self.tasks:
            category_counts[task["category"]] = category_counts.get(task["category"], 0) + 1
        return {
            "models": self.models,
            "tasks": self.tasks,
            "categories": category_counts,
            "maxJobs": LOCAL_MAX_JOBS,
            "maxJobsExternal": EXTERNAL_MAX_JOBS,
            # 裁判从"一个勾选框"变成"一组可选的尺 + 不启用"：面板要能把每一档缺不缺密钥也显示出来。
            "judges": [judge_view(name, cfg, self.judge_default)
                       for name, cfg in sorted(self.judges.items())],
            "judgeDefault": self.judge_default,
            "banks": [{"name": bank["name"], "count": len(bank["tasks"]),
                       "description": bank["description"]} for bank in load_banks()],
            "controls": {"pauseResume": os.name == "posix", "safePointPause": True},
        }

    def _profile_by_identity(self, identity: str) -> dict | None:
        """按 ``档案名:model`` 找回那把尺；找不到返回 None（档案改名/删掉了）。"""
        for name, cfg in self.judges.items():
            if f"{name}:{cfg.get('model', '?')}" == identity:
                profile = dict(cfg)
                profile["name"] = name
                return profile
        return None

    def _require_judge_ready(self, profile: dict) -> dict:
        """这把尺的密钥此刻在不在。缺就一句话拦住，别跑完整轮再交出一堆未评。"""
        ready, source = key_state(str(profile.get("api_key", "")))
        if not ready:
            var = source[4:] if source.startswith("env:") else source
            raise ValueError(f"裁判 {profile.get('name')} 缺密钥（{source}）：把 {var} 写进仓库根的 "
                             f".env 后重启本服务，或在面板把裁判选成「不启用」")
        return profile

    def resolve_judge(self, choice) -> dict | None:
        """发车面板发来的``judge``字段 → 可用裁判档案；返回 None 表示这轮不开裁判。

        默认档由 models.yaml 的 ``judge.default`` 决定（现默认 mimo-flash），所以面板上"跟随
        默认"与"点名某一把"是两件事：前者跟着表走，后者钉死——分数只有同尺才可比。
        """
        parsed = parse_judge_choice(choice)
        if parsed is None:
            return None
        name = self.judge_default if parsed == JUDGE_DEFAULT else parsed
        if not self.judges:
            raise ValueError("models.yaml 里没有可用的裁判档案（judge.profiles），无法启用裁判")
        if not name or name not in self.judges:
            raise ValueError("未知裁判档案「" + str(name or "?") + "」；可用："
                             + ", ".join(sorted(self.judges)))
        profile = dict(self.judges[name])
        profile["name"] = name
        return self._require_judge_ready(profile)

    def run_judge(self, manifest: dict, *, enabled: bool) -> tuple[dict | None, str]:
        """续跑/重判用的裁判档案：优先这个 run 原本那把尺（manifest 里的 ``档案名:model``）。

        原尺已从档案表里消失时退到默认档并带回一句话给日志——不硬拦，因为续跑的大头成本在
        被测端；换尺的后果由题面 ``judge_id`` 闸门兜着：对不上一律记未评，不会悄悄混进同一张榜。
        """
        identity = str(manifest.get("judge_profile") or "")
        note = ""
        profile = self._profile_by_identity(identity) if identity else None
        if profile is None:
            name = self.judge_default
            if not name or name not in self.judges:
                raise ValueError("models.yaml 的 judge.default 不可用，这一轮补不了裁判")
            profile = dict(self.judges[name])
            profile["name"] = name
            if identity:
                note = f"原运行的裁判尺 {identity} 已不在 models.yaml 的档案表里，改用默认档 {name}"
        profile = self._require_judge_ready(profile)
        return (profile, note) if enabled else (None, note)

    def _scope_task_ids(self, scope: str) -> list[str]:
        """把 "a,b,c" 形式的任务清单解析成合法题号列表；不合法就返回空。"""
        ids = [piece.strip() for piece in str(scope or "").split(",") if piece.strip()]
        return ids if ids and all(task_id in self.task_ids for task_id in ids) else []

    def concurrency(self) -> dict:
        """当前 run 的真实并发设定：读它自己的 control.json，读不到就报"不适用"。

        为什么不拿 state["jobs"] 当真相：那只是**开跑时**的设定，热调之后它就过时了。
        闸会把生效值回写进 control.json，所以那里才是实盘。
        """
        with self.lock:
            output_dir = self.state.get("outputDir")
            status = self.state.get("status")
            started_jobs = self.state.get("jobs")
        payload = {"applicable": bool(output_dir) and status in ACTIVE_STATUSES,
                   "status": status, "startedJobs": started_jobs, "jobs": None,
                   "ceiling": None, "controlPath": None, "source": None}
        if not output_dir:
            return payload
        path = ROOT / output_dir / "control.json"
        payload["controlPath"] = display_path(path)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            payload["jobs"] = data.get("jobs")
            payload["ceiling"] = data.get("max_jobs")
            payload["source"] = data.get("source")
        return payload

    def set_jobs(self, jobs: int) -> dict:
        """热调当前 run 的并发：写 control.json，由 cli 的闸在题目边界上生效。

        只在任务边界生效，所以这里不改任何在跑的题——真需要"立刻少几个并发"时，
        多出来的那几道会跑完自己剩下的步骤。
        """
        # 先校验入参再查 run 状态：否则"并发数填错了"会被报成"没有 run 在跑"，
        # 前端按 409 提示用户去发车，而真正的问题是那个数字。
        try:
            jobs = int(jobs)
        except (TypeError, ValueError) as exc:
            raise ValueError("并发数必须是整数") from exc
        with self.lock:
            if self.state["status"] not in ACTIVE_STATUSES:
                raise RuntimeError("当前没有正在运行的评测，并发设定只对进行中的 run 生效")
            output_dir = self.state.get("outputDir")
            profile = next((model for model in self.models
                            if model["name"] == self.state.get("profile")), None)
            ceiling = (profile or {}).get("maxJobs") or LOCAL_MAX_JOBS
        if not 1 <= jobs <= ceiling:
            raise ValueError(f"这个端点的并发只能是 1–{ceiling}")
        path = (ROOT / output_dir / "control.json").resolve()
        if (ROOT / output_dir).resolve() not in path.parents:
            raise ValueError("控制文件越界")
        current = {}
        if path.is_file():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current = {}
        payload = {**current, "jobs": jobs, "max_jobs": ceiling, "source": "web"}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        self._append_log(f"[web] 并发热调 -> {jobs}（{display_path(path)}；在跑的题不打断）")
        return {"ok": True, **self.concurrency()}

    def _append_log(self, line: str, stream: str = "run") -> None:
        clean = line.rstrip("\r\n")
        if not clean:
            return
        with self.lock:
            self.log_sequence += 1
            self.logs.append({"seq": self.log_sequence, "stream": stream, "line": clean[-6000:]})

    def _events_log_path(self):
        """当前 run 的事件流水文件。目录由启动时定的 --out 给出，取不到就不落盘。"""
        out_dir = self.state.get("outputDir")
        if not out_dir:
            return None
        path = (ROOT / str(out_dir)).resolve()
        if ROOT.resolve() not in path.parents:
            return None
        return path / "events.jsonl"

    def _persist_event(self, record: dict) -> None:
        # 逐条 append：高频路径不能因为磁盘抖一下把实时流卡住。
        path = self._events_log_path()
        if path is None:
            return
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def reset_events_log(self) -> None:
        """新 run 开一份事件流水，顺带清掉上一个 run 的内存缓冲。"""
        self.events.clear()
        self.event_sequence = 0
        path = self._events_log_path()
        if path is None:
            return
        try:
            path.write_text("", encoding="utf-8")
        except OSError:
            pass

    def load_events_from_disk(self) -> int:
        """服务重启后从 events.jsonl 回灌最近一次 run，历史轨迹在控制台里还能看。"""
        runs_root = ROOT / "runs"
        if not runs_root.is_dir():
            return 0
        best = None
        for cand in sorted(runs_root.glob("web-*/events.jsonl"), key=lambda f: f.stat().st_mtime):
            best = cand
        if best is None:
            return 0
        seq = 0
        loaded = 0
        with open(best, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("task"):
                    self.events.append(record)
                    seq = max(seq, int(record.get("seq") or 0))
                    loaded += 1
        while len(self.events) > 8000:
            self.events.popleft()
        self.event_sequence = seq
        self.state["id"] = best.parent.name          # 前端按 current 取，必须对上
        if self.state.get("status") == "idle":       # 回灌的是已结束的历史 run，状态要说得出口
            self.state["status"] = "completed"
        return loaded

    def events_run(self, run_id: str, cursor: int, limit: int) -> dict:
        """从磁盘读某个 run 的事件流水：刷新页面、重启服务、翻历史 run 都走这条。"""
        path = (ROOT / "runs" / run_id / "events.jsonl").resolve()
        if (ROOT / "runs").resolve() not in path.parents or not path.exists():
            return {"runId": run_id, "status": None, "lastSeq": 0, "cursor": cursor,
                    "truncated": False, "pending": 0, "events": []}
        items = []
        last = 0
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("task"):
                    seq = int(record.get("seq") or 0)
                    last = max(last, seq)
                    if seq > cursor:
                        items.append(record)
        batch = items[:limit]
        status = self.state.get("status") if self.state.get("id") == run_id else "completed"
        return {"runId": run_id, "status": status, "lastSeq": last,
                "cursor": batch[-1]["seq"] if batch else cursor,
                "truncated": len(items) > limit, "pending": max(0, len(items) - len(batch)),
                "events": batch}

    def events_since(self, cursor: int, limit: int = 400) -> dict:
        """给控制台的实时事件拉取：从 cursor 之后取一批，并告诉它缓冲有没有被截断。

        用游标式拉取而不是 SSE：这个服务是手写的 ThreadingHTTPServer，每连接一线程，
        长连接推送要额外兼顾 HTTP/1.0 的关闭语义与断连回收；而拉取只要一个游标，
        400ms 拿一批合并过的增量，看上去就是逐字出来。
        """
        with self.lock:
            items = list(self.events)
            seq = self.event_sequence
            status = self.state.get("status")
            run_id = self.state.get("id")
        window = [item for item in items if item["seq"] > cursor]
        # 缓冲是环形队列：客户端落后太多时最旧的那批已经被挤掉了。这种情况（以及首次
        # 连接）给**最近**一段而不是从最旧的追起——看屏的人要的是"现在在干什么"，
        # 中间丢掉的细节本来就在轨迹文件里。
        evicted = bool(items) and cursor > 0 and items[0]["seq"] > cursor + 1
        batch = window[-limit:] if (evicted or cursor == 0) else window[:limit]
        return {
            "runId": run_id,
            "status": status,
            "lastSeq": seq,
            "truncated": evicted or len(window) > limit,
            "cursor": batch[-1]["seq"] if batch else cursor,
            "pending": max(0, len(window) - len(batch)),
            "events": batch,
        }

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            data = dict(self.state)
            if data["status"] in ACTIVE_STATUSES and data["startedAt"]:
                now = time.time()
                paused = data.get("pausedSeconds", 0)
                if data.get("pausedAt") is not None:
                    paused += now - data["pausedAt"]
                data["elapsedSeconds"] = round(now - data["startedAt"] - paused, 1)
            data["logs"] = [item for item in self.logs if item["seq"] > since]
            data["lastLogSequence"] = self.log_sequence
            # 运行台里的"并发"必须是闸实际生效的值，而不是 state["jobs"] 那个发车时的快照：
            # 热调之后旧数字会让人以为还是 1 个 worker。
            data["concurrency"] = self.concurrency()
            return data

    def start(self, request: dict) -> dict:
        profile = str(request.get("profile") or request.get("model") or "")
        model_name = str(request.get("modelName") or profile).strip()
        profile_config = next((model for model in self.models if model["name"] == profile), None)
        api_model = str(request.get("apiModel") or (profile_config or {}).get("apiModel") or "").strip()
        try:
            max_output_tokens = int(request.get("maxOutputTokens", (profile_config or {}).get("maxOutputTokens", 16384)))
            request_timeout_s = int(request.get("requestTimeoutSeconds", (profile_config or {}).get("timeoutSeconds", 900)))
        except (TypeError, ValueError) as exc:
            raise ValueError("最大 token 和请求超时必须是整数") from exc
        scope = str(request.get("scope", "all"))
        if scope.startswith("bank:"):
            want = scope.split(":", 1)[1].strip()
            hit = next((bk for bk in load_banks() if bk["name"] == want), None)
            if hit is None:
                avail = ", ".join(bk["name"] for bk in load_banks()) or "(空)"
                raise ValueError(f"未知题库「{want}」，可用：{avail}")
            scope = ",".join(hit["tasks"])
        # 发车面板的"外部端点三件套"：临时把这次运行指向另一个端点（base_url / 密钥 /
        # 协议）。不写进 models.yaml 也能跑，换一把临时 key 不必改文件、不必重启服务。
        base_url_override = str(request.get("baseUrl") or "").strip()
        api_key_input = str(request.get("apiKey") or "").strip()
        wire_api_input = str(request.get("wireApi") or "").strip().lower()
        if wire_api_input and wire_api_input not in ("chat", "responses"):
            raise ValueError("协议只能选 chat 或 responses")
        if base_url_override:
            parsed = urlsplit(base_url_override)
            if len(base_url_override) > 300 or any(ord(char) < 32 for char in base_url_override):
                raise ValueError("端点地址不能包含控制字符或超过 300 字符")
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("端点地址要写全，例如 https://api.example.com/v1")
        if len(api_key_input) > 400 or any(ord(char) < 32 for char in api_key_input):
            raise ValueError("API Key 不能包含换行、制表符或超过 400 字符")
        # 看板的"跑这 N 题"走 tasks：一组题号一次发车，而不是只能一道或一类。
        requested_tasks = request.get("tasks")
        if requested_tasks is not None and not isinstance(requested_tasks, list):
            raise ValueError("tasks 必须是任务号数组")
        # 裁判是一档可选的尺：面板发 "default" / 档案名 / "off"；看板发车不带这个字段 =
        # 开着用默认档。具体取哪一档只有 models.yaml 知道，这里不再硬编码任何一把。
        judge_choice = request.get("judge", True)
        try:
            jobs = int(request.get("jobs", 4))
        except (TypeError, ValueError) as exc:
            raise ValueError("并发数必须是整数") from exc
        if profile not in self.model_names:
            raise ValueError("未知连接配置")
        # 并发天花板按**这次真实打的上游**算：选了本地连接配置但把 base_url 指向外部，
        # 那就是外部端点，不该还被本地那 4 个 worker 卡着；反过来把云端 API 指到本机转发器
        # （upstream_url）也不该被当成本地。地址本身（effective_base_url）仍按实际连接目标显示。
        effective_base_url = base_url_override or (profile_config or {}).get("endpoint", "")
        upstream_target = classify_target(profile_config or {}, base_url_override)
        external = endpoint_kind(upstream_target) == "external"
        if external and not api_key_input and not (profile_config or {}).get("keyReady"):
            missing = (profile_config or {}).get("keySource") or "连接配置里的密钥"
            raise ValueError(
                f"{upstream_target} 是外部端点，需要 API Key：填在「外部端点」里发一次車，"
                f"或把 {missing} 写进仓库根的 .env（.gitignore 已盖住）再重启服务"
            )
        # 外部端点 + 本地占位 model id = 整轮 50 道题一起 400（2026-09-24 真踩过，
        # 还被记成 50 个 FAIL 进了榜）。这种坑必须在开跑前用一句话拦住。
        if external and (not api_model or api_model.lower() in {"local", "default"}):
            raise ValueError(
                f"外部端点不能把 model id 留成本地占位「{api_model or '空'}」："
                f"改成对面真实的模型名（例如小米是 mimo-v2.6-pro），"
                f"或者直接选 models.yaml 里对应的连接配置"
            )
        ceiling = max_jobs_for(upstream_target)
        if requested_tasks:
            unknown = [t for t in requested_tasks if str(t) not in self.task_ids]
            if unknown:
                raise ValueError(f"未知任务：{', '.join(map(str, unknown[:5]))}")
            if len(set(requested_tasks)) != len(requested_tasks):
                raise ValueError("任务清单里有重复")
            scope = ",".join(str(t) for t in requested_tasks)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", model_name):
            raise ValueError("模型名称只能使用 1–64 位字母、数字、点、下划线或连字符")
        if not api_model or len(api_model) > 160 or any(ord(char) < 32 for char in api_model):
            raise ValueError("API Model ID 不能为空、包含控制字符或超过 160 字符")
        if not 256 <= max_output_tokens <= 262144:
            raise ValueError("单次响应最大 token 必须在 256–262144 之间")
        if not 10 <= request_timeout_s <= 3600:
            raise ValueError("模型请求超时必须在 10–3600 秒之间")
        if scope != "all" and scope not in self.categories and not self._scope_task_ids(scope):
            raise ValueError("未知任务范围")
        if not 1 <= jobs <= ceiling:
            raise ValueError(f"这个端点的并发只能是 1–{ceiling}")
        # 尺子在发车前定下来并验过密钥：跑完整轮才发现裁判没配好，交出的是几十个「未评」。
        judge_profile = self.resolve_judge(judge_choice)

        with self.lock:
            if self.state["status"] in ACTIVE_STATUSES:
                raise RuntimeError("已有评测正在运行")
            run_id = "web-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            output_dir = ROOT / "runs" / run_id
            scoped_tasks = self._scope_task_ids(scope)
            total = len(self.tasks) if scope == "all" else (
                sum(task["category"] == scope for task in self.tasks)
                if scope in self.categories else (len(scoped_tasks) or 1)
            )
            command = [
                sys.executable, "-u", str(ROOT / "cli.py"), "run",
                "--models", profile,
                "--model-label", model_name,
                "--api-model", api_model,
                "--max-output-tokens", str(max_output_tokens),
                "--request-timeout-s", str(request_timeout_s),
                "--out", str(output_dir),
                "--jobs", str(jobs),
            ]
            if scope != "all":
                command.extend(["--tasks", scope])
            if base_url_override:
                command.extend(["--base-url", base_url_override])
            if wire_api_input:
                command.extend(["--wire-api", wire_api_input])
            if api_key_input:
                # 密钥只进子进程环境，不进 argv：ps / shell 历史 / 日志里都只会看到变量名。
                command.extend(["--api-key-env", WEB_KEY_ENV])
            if judge_profile:
                # 把档案名显式传下去：以后改了 judge.default，已发车的这一轮不该跟着换尺。
                command.extend(["--judge", judge_profile["name"]])
            # 控制台的实时思考流/工具调用：引擎按 [ev] 行打到 stdout，由本服务收下。
            command.append("--events")
            key_env = {WEB_KEY_ENV: api_key_input} if api_key_input else {}
            state_endpoint_label = "外部" if external else "本地"
            state_key_label = "面板临时密钥" if api_key_input else ((profile_config or {}).get("keySource") or "无")
            self.logs.clear()
            self.log_sequence = 0
            self.events.clear()
            self.event_sequence = 0
            self.cancel_requested = False
            self.state = {
                "id": run_id,
                "status": "starting",
                "model": model_name,
                "profile": profile,
                "apiModel": api_model,
                "maxOutputTokens": max_output_tokens,
                "requestTimeoutSeconds": request_timeout_s,
                "scope": scope,
                "jobs": jobs,
                "judge": bool(judge_profile),
                # 本轮的尺是哪把（档案名:model）；未启用时 null。面板与控制台都要拿它对账。
                "judgeProfile": judge_identity(judge_profile) if judge_profile else None,
                # 端点与密钥来源只留"是什么"，不留密钥本身：这份 state 会整个发给浏览器。
                "baseUrl": effective_base_url,
                "endpoint": "external" if external else "local",
                "wireApi": wire_api_input or (profile_config or {}).get("wireApi") or None,
                "keySource": "面板临时密钥" if api_key_input else (profile_config or {}).get("keySource"),
                "completed": 0,
                "passed": 0,
                "failed": 0,
                "ungraded": 0,
                "total": total,
                "startedAt": time.time(),
                "endedAt": None,
                "elapsedSeconds": 0,
                "outputDir": str(output_dir.relative_to(ROOT)),
                "reportReady": False,
                "exitCode": None,
                "currentTasks": [],
                "taskRuns": [],
                "totalTokens": 0,
                "totalSteps": 0,
                "totalTaskSeconds": 0,
                "controlReady": False,
                "pausedAt": None,
                "pausedSeconds": 0,
            }
            self.reset_events_log()
            self._append_log(
                f"[web] 启动 {run_id} · {model_name} · profile={profile} · api_model={api_model} · "
                f"max_output_tokens={max_output_tokens} · request_timeout={request_timeout_s}s · scope={scope} · jobs={jobs} · "
                f"endpoint={effective_base_url}（{state_endpoint_label}）· key={state_key_label}"
            )
            thread = threading.Thread(target=self._run, args=(command, output_dir, key_env), daemon=True, name=f"run-{run_id}")
            thread.start()
            return self.snapshot()

    def resume_run(self, run_id: str, task_id: str | None = None) -> dict:
        """Resume missing tasks or rerun one task inside an existing Web run."""
        if not re.fullmatch(r"web-[A-Za-z0-9._-]+", run_id):
            raise ValueError("无效运行 ID")
        run_dir = (ROOT / "runs" / run_id).resolve()
        if (ROOT / "runs").resolve() not in run_dir.parents:
            raise ValueError("运行目录越界")
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("运行记录缺少有效 manifest") from exc
        models = list(manifest.get("models", []))
        if len(models) != 1:
            raise ValueError("Web 增量续跑目前只支持单模型 run")
        model_name = models[0]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", model_name):
            raise ValueError("原运行的模型名称无法安全续跑")
        runtime = (manifest.get("model_runtime") or {}).get(model_name, {})
        profile = profile_for_runtime(self.models, runtime)
        if profile is None:
            raise ValueError(
                f"找不到与原运行 endpoint（{runtime.get('base_url') or '未知'}）匹配的连接配置："
                "面板里临时填的外部端点不在 models.yaml 里，续跑没法重建密钥。"
                "把它写成一条连接配置（密钥用 ${VAR} 引用放 .env）就能续跑。"
            )
        task_ids = [value for value in manifest.get("tasks", []) if value in self.task_ids]
        if not task_ids:
            raise ValueError("原运行没有可执行的当前任务")
        model_dir = _model_directory(run_dir, model_name)
        existing: dict[str, dict] = {}
        if model_dir and model_dir.exists():
            for result_path in model_dir.glob("*/result.json"):
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                existing[result.get("task_id", result_path.parent.name)] = result
        if task_id is not None:
            if task_id not in task_ids:
                raise ValueError("该任务不属于此运行")
            selected = [task_id]
            mode = "rerun"
        else:
            selected = [value for value in task_ids if value not in existing]
            if not selected:
                raise RuntimeError("此运行没有缺失任务")
            mode = "resume"
        # 续跑也用这次的端点算天花板：外部 API 的 run 不该被本地那 4 个 worker 压回去，
        # 指到本机转发器的外部 run 也不该（manifest 里 upstream_url 才是真实上游）。
        jobs = max(1, min(max_jobs_for(runtime.get("upstream_url") or runtime.get("base_url")
                                        or profile.get("endpoint", "")),
                          int(manifest.get("jobs", 4))))
        max_output_tokens = int(runtime.get("max_output_tokens") or profile.get("maxOutputTokens") or 16384)
        request_timeout_s = int(profile.get("timeoutSeconds") or 900)
        api_model = str(runtime.get("model") or profile.get("apiModel") or "local")
        # 续跑沿用这一轮原本那把尺；老 run 的 manifest 没记档案，就按"跑过裁判"的轨迹字段兜底。
        judge_profile, judge_note = self.run_judge(
            manifest, enabled=bool(manifest.get("judge"))
            or any(result.get("judge") for result in existing.values()))
        command = [
            sys.executable, "-u", str(ROOT / "cli.py"), "run",
            "--models", profile["name"],
            "--model-label", model_name,
            "--api-model", api_model,
            "--max-output-tokens", str(max_output_tokens),
            "--request-timeout-s", str(request_timeout_s),
            "--out", str(run_dir),
            "--tasks", ",".join(selected),
            "--jobs", str(jobs),
            "--merge-run",
            # 续跑也要有实时流：否则续跑的那几题在控制台里只能看进度行，看不到思考。
            "--events",
        ]
        if judge_profile:
            command.extend(["--judge", judge_profile["name"]])

        task_runs, counters = self._summarize_existing(task_ids, existing, selected)

        with self.lock:
            if self.state["status"] in ACTIVE_STATUSES:
                raise RuntimeError("已有评测正在运行")
            self.logs.clear()
            self.log_sequence = 0
            self.events.clear()
            self.event_sequence = 0
            self.cancel_requested = False
            self.state = {
                "id": run_id, "status": "starting", "model": model_name,
                "profile": profile["name"], "apiModel": api_model,
                "maxOutputTokens": max_output_tokens, "requestTimeoutSeconds": request_timeout_s,
                "scope": "all", "jobs": jobs, "judge": bool(judge_profile),
                "judgeProfile": judge_identity(judge_profile) if judge_profile else None,
                "mode": mode, "retryTasks": selected,
                "completed": counters["completed"], "passed": counters["passed"],
                "failed": counters["failed"], "ungraded": counters["ungraded"],
                "total": len(task_ids),
                "startedAt": time.time(), "endedAt": None, "elapsedSeconds": 0,
                "outputDir": str(run_dir.relative_to(ROOT)), "reportReady": False,
                "exitCode": None, "currentTasks": [], "taskRuns": task_runs,
                "totalTokens": counters["tokens"], "totalSteps": counters["steps"],
                "totalTaskSeconds": counters["taskSeconds"], "controlReady": False,
                "pausedAt": None, "pausedSeconds": 0,
            }
            action = "重跑" if mode == "rerun" else "续跑"
            self.reset_events_log()
            self._append_log(
                f"[web] {action} {run_id} · {model_name} · tasks={','.join(selected)} · "
                f"jobs={jobs} · request_timeout={request_timeout_s}s"
            )
            if judge_note:
                self._append_log("[web][warn] " + judge_note)
            thread = threading.Thread(target=self._run, args=(command, run_dir), daemon=True,
                                      name=f"{mode}-{run_id}")
            thread.start()
            return self.snapshot()

    def _summarize_existing(self, task_ids: list[str], existing: dict[str, dict],
                            selected: list[str]) -> tuple[list[dict], dict]:
        """把已落盘的 attempt 摊成面板那一列。

        selected 里的题算"待处理"，不进计数：续跑时它们要重采，重判时它们只缺判决。
        未评（graded=false）单独计数、不给 PASS/FAIL——那是跑过但没结论的一次尝试，
        把它显示成 FAIL 就等于把云端的故障记在模型头上。
        """
        metadata = {task["id"]: task for task in self.tasks}
        task_runs = []
        counters = {"completed": 0, "passed": 0, "failed": 0, "ungraded": 0,
                    "tokens": 0, "steps": 0, "taskSeconds": 0.0}
        for index, task_id in enumerate(task_ids, 1):
            meta = metadata.get(task_id, {})
            base = {"index": index, "taskId": task_id,
                    "title": meta.get("title", task_id), "category": meta.get("category", "unknown")}
            result = existing.get(task_id)
            if result is None or task_id in selected:
                task_runs.append({**base, "status": "pending", "result": None, "steps": None,
                                  "tokens": None, "elapsedSeconds": None, "failure": None})
                continue
            efficiency = result.get("efficiency", {})
            graded = bool(result.get("graded", True))
            is_pass = bool(result.get("passed"))
            counters["completed"] += 1
            counters["ungraded"] += 0 if graded else 1
            counters["passed"] += is_pass and graded
            counters["failed"] += (not is_pass) and graded
            counters["tokens"] += int(efficiency.get("tokens", 0))
            counters["steps"] += int(efficiency.get("steps", 0))
            counters["taskSeconds"] += float(efficiency.get("elapsed_s", 0))
            task_runs.append({
                **base, "status": "completed",
                "result": None if not graded else ("PASS" if is_pass else "FAIL"),
                "graded": graded, "ungradedReason": result.get("ungraded_reason"),
                "steps": int(efficiency.get("steps", 0)), "tokens": int(efficiency.get("tokens", 0)),
                "elapsedSeconds": float(efficiency.get("elapsed_s", 0)),
                "failure": result.get("failure_reason"),
            })
        counters["taskSeconds"] = round(counters["taskSeconds"], 2)
        return task_runs, counters

    def rejudge_run(self, run_id: str) -> dict:
        """重判：只补跑裁判闸门，把因裁判故障记「未评」的 attempt 判完，不重新采样。

        与续跑的分工很清楚——采样本身没跑成（上游 401、连不上）的 attempt 缺的是轨迹，
        只能重跑；已经有一条完整轨迹、只差半边评分标准的，才轮到重判。前者在这里
        直接被 list_attempts 的 rejudgeable 挡掉，不会偷偷烧 token 去救一个救不回来的东西。
        """
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", run_id):
            raise ValueError("无效运行 ID")
        run_dir = (ROOT / "runs" / run_id).resolve()
        if (ROOT / "runs").resolve() not in run_dir.parents:
            raise ValueError("运行目录越界")
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("运行记录缺少有效 manifest") from exc
        models = list(manifest.get("models", []))
        if len(models) != 1:
            raise ValueError("重判目前只支持单模型 run")
        model_name = models[0]
        # 重判补的就是判决，必须有尺：优先这个 run 原本那把（manifest.judge_profile）。
        judge_profile, judge_note = self.run_judge(manifest, enabled=True)
        attempts = list_attempts(run_dir, [model_name], None, only_ungraded=True)
        candidates = [item for item in attempts if item["rejudgeable"]]
        skipped = len(attempts) - len(candidates)
        if not candidates:
            raise RuntimeError("此 run 没有可重判的未评 attempt"
                               + (f"（另有 {skipped} 条是采样本身没跑成，只能重跑）" if skipped else ""))
        task_ids = [value for value in manifest.get("tasks", []) if value in self.task_ids]
        selected = [item["task_id"] for item in candidates]
        existing: dict[str, dict] = {}
        for result_path in (run_dir / model_name).glob("*/result.json"):
            try:
                record = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            existing[record.get("task_id", result_path.parent.name)] = record
        task_runs, counters = self._summarize_existing(task_ids, existing, selected)
        # 重判不改采样事实：这些题的步数/token/耗时就是归档那次的。先把数字摆上，
        # [done] 到达时才有东西可并进总计（面板上也不会出现"— 步"）。
        for record in task_runs:
            result = existing.get(record["taskId"])
            if record["status"] == "pending" and result:
                efficiency = result.get("efficiency", {})
                record.update({"steps": int(efficiency.get("steps", 0)),
                               "tokens": int(efficiency.get("tokens", 0)),
                               "elapsedSeconds": float(efficiency.get("elapsed_s", 0))})
        jobs = max(1, min(4, len(selected)))
        command = [sys.executable, "-u", str(ROOT / "cli.py"), "rejudge",
                   "--out", str(run_dir), "--models", model_name, "--jobs", str(jobs),
                   "--judge", judge_profile["name"]]
        with self.lock:
            if self.state["status"] in ACTIVE_STATUSES:
                raise RuntimeError("已有评测正在运行")
            self.logs.clear()
            self.log_sequence = 0
            self.events.clear()
            self.event_sequence = 0
            self.cancel_requested = False
            self.state = {
                "id": run_id, "status": "starting", "model": model_name,
                # 重判没有被测模型，这里的 apiModel 就填实际用了的那把尺。
                "profile": "judge", "apiModel": judge_identity(judge_profile),
                "maxOutputTokens": None, "requestTimeoutSeconds": None,
                "scope": "all", "jobs": jobs, "judge": True,
                "judgeProfile": judge_identity(judge_profile),
                "mode": "rejudge", "retryTasks": selected, "skippedUnsamplable": skipped,
                "completed": counters["completed"], "passed": counters["passed"],
                "failed": counters["failed"], "ungraded": counters["ungraded"],
                "total": len(task_ids),
                "startedAt": time.time(), "endedAt": None, "elapsedSeconds": 0,
                "outputDir": str(run_dir.relative_to(ROOT)), "reportReady": False,
                "exitCode": None, "currentTasks": [], "taskRuns": task_runs,
                "totalTokens": counters["tokens"], "totalSteps": counters["steps"],
                "totalTaskSeconds": counters["taskSeconds"], "controlReady": False,
                "pausedAt": None, "pausedSeconds": 0,
            }
            self.reset_events_log()
            self._append_log(
                f"[web] 重判 {run_id} · {model_name} · 待补判={','.join(selected)} · "
                f"跳过(采样未跑成)={skipped} · jobs={jobs}"
            )
            if judge_note:
                self._append_log("[web][warn] " + judge_note)
            thread = threading.Thread(target=self._run, args=(command, run_dir), daemon=True,
                                      name=f"rejudge-{run_id}")
            thread.start()
            return self.snapshot()

    def _run(self, command: list[str], output_dir: Path, extra_env: dict[str, str] | None = None) -> None:
        try:
            with self.lock:
                if self.cancel_requested:
                    self.state["status"] = "cancelled"
                    self.state["endedAt"] = time.time()
                    return
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            # 面板临时密钥走这里注入：只在这个子进程的环境里存在，命令行上只有变量名。
            env.update(extra_env or {})
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            with self.lock:
                self.process = process
                self.state["pid"] = process.pid
                should_cancel = self.cancel_requested
                self.state["status"] = "cancelling" if should_cancel else "running"
            if should_cancel:
                os.killpg(process.pid, signal.SIGTERM)
            assert process.stdout is not None
            for line in process.stdout:
                self._append_log(line)
                try:
                    self._consume_progress(line)
                except Exception as exc:  # noqa: BLE001
                    # 进度解析炸了不能顺手掐断读管道：子进程还在往 result.json 里写，
                    # 读到底才是这一轮真正的账。少刷一张卡片比把一次还在跑的评测判成 failed 便宜。
                    self._append_log(
                        f"[web:error] 进度行无法解析 {type(exc).__name__}: {exc} | {line[:120]}", "error")
            exit_code = process.wait()
            with self.lock:
                self.state["exitCode"] = exit_code
                self.state["endedAt"] = time.time()
                paused = self.state.get("pausedSeconds", 0)
                if self.state.get("pausedAt") is not None:
                    paused += self.state["endedAt"] - self.state["pausedAt"]
                self.state["elapsedSeconds"] = round(self.state["endedAt"] - self.state["startedAt"] - paused, 1)
                if self.cancel_requested:
                    final_status = "cancelled"
                else:
                    final_status = "completed" if exit_code == 0 else "failed"
                self.state["status"] = "finalizing" if final_status == "completed" else final_status
                self.process = None
            self._generate_report(output_dir)
            with self.lock:
                if self.state["status"] == "finalizing":
                    self.state["status"] = "completed"
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"[web:error] {type(exc).__name__}: {exc}", "error")
            with self.lock:
                self.state["status"] = "failed"
                self.state["endedAt"] = time.time()
                self.state["exitCode"] = -1
                self.process = None

    def _consume_progress(self, line: str) -> None:
        started = re.search(r"\[run (\d+)/(\d+)\]\s+\S+\s+::\s+(\S+)", line)
        # cli 对未评（上游故障/裁判没判成）的尝试打的是 "未评" 而不是 PASS/FAIL。
        # 这里少收一个分支的后果是：那道题永远留在 currentTasks 里——[end] 行没人消费，
        # 看板卡片就钉死在"运行中"列，跑完一轮也走不掉（2026-09-23 裁判 429 真实踩到）。
        ended = re.search(
            r"\[end (\d+)/(\d+)\]\s+\S+\s+::\s+(\S+)\s+(PASS|FAIL|未评)\s+"
            r"steps=(\d+)\s+tokens=(\d+)\s+t=([\d.]+)s\s*(.*)", line,
        )
        # 重判的进度行：`[done 1/4] mimo :: memory-loop-001  未评 -> PASS judge=1.0`。
        # 它不带 steps/tokens——采样事实没变，变的只是判决，效率数字沿用归档的那次尝试。
        rejudged = re.search(r"\[done (\d+)/(\d+)\]\s+\S+\s+::\s+(\S+)\s+\S+\s+->\s+(PASS|FAIL|未评)",
                             line)
        with self.lock:
            if line.startswith("[control] ready"):
                self.state["controlReady"] = True
            if line.startswith("[ev] "):
                # 实时事件：单条解析失败不能拖垮整个进度解析，丢一条比卡住一轮便宜得多。
                try:
                    payload = json.loads(line[5:].strip())
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and payload.get("task"):
                    self.event_sequence += 1
                    record = {"seq": self.event_sequence, **payload}
                    self.events.append(record)
                    self._persist_event(record)
            if started:
                index, _total, task_id = started.groups()
                if task_id not in self.state["currentTasks"]:
                    self.state["currentTasks"].append(task_id)
                task_meta = next((task for task in self.tasks if task["id"] == task_id), {})
                record = next((item for item in self.state["taskRuns"] if item["taskId"] == task_id), None)
                if record is not None:
                    record.update({"status": "running", "result": None, "steps": None,
                                   "tokens": None, "elapsedSeconds": None, "failure": None,
                                   "startedAt": time.time()})
                else:
                    self.state["taskRuns"].append({
                        "index": int(index), "taskId": task_id,
                        "title": task_meta.get("title", task_id),
                        "category": task_meta.get("category", "unknown"),
                        "status": "running", "result": None, "steps": None,
                        "tokens": None, "elapsedSeconds": None, "failure": None,
                        # 开跑时间戳：卡片点开后要说得出"这道题已经跑了多久"，而不是只给个 RUN。
                        "startedAt": time.time(),
                    })
            if ended:
                index, _total, task_id, mark, steps, tokens, elapsed, failure = ended.groups()
                self.state["currentTasks"] = [item for item in self.state["currentTasks"] if item != task_id]
                # 未评的尝试也跑完了（只是没判成），进度照算；但 passed/failed 只收真判定。
                self.state["completed"] += 1
                self.state["totalTokens"] += int(tokens)
                self.state["totalSteps"] += int(steps)
                self.state["totalTaskSeconds"] = round(self.state["totalTaskSeconds"] + float(elapsed), 2)
                record = next((item for item in self.state["taskRuns"] if item["taskId"] == task_id), None)
                if record is None:
                    record = {"index": int(index), "taskId": task_id, "title": task_id, "category": "unknown"}
                    self.state["taskRuns"].append(record)
                record.update({
                    # 未评不是结论：result 留空，前端按"未评"显示而不是当成某种判定。
                    "status": "completed", "result": None if mark == "未评" else mark,
                    "steps": int(steps), "tokens": int(tokens),
                    "elapsedSeconds": float(elapsed), "failure": failure or None,
                })
                if mark == "PASS":
                    self.state["passed"] += 1
                elif mark == "FAIL":
                    self.state["failed"] += 1

            if rejudged:
                _index, _total, task_id, mark = rejudged.groups()
                self.state["currentTasks"] = [item for item in self.state["currentTasks"] if item != task_id]
                record = next((item for item in self.state["taskRuns"] if item["taskId"] == task_id), None)
                if record is None:
                    record = {"index": 0, "taskId": task_id, "title": task_id, "category": "unknown",
                              "steps": 0, "tokens": 0, "elapsedSeconds": 0.0}
                    self.state["taskRuns"].append(record)
                graded = mark != "未评"
                # 这一条 attempt 之前挂在"没结论"上，现在才第一次进计数。效率数字沿用
                # 归档的那次采样（重判不重跑模型），所以它们在 pending 时就填好了。
                self.state["completed"] += 1
                self.state["totalTokens"] += int(record.get("tokens") or 0)
                self.state["totalSteps"] += int(record.get("steps") or 0)
                self.state["totalTaskSeconds"] = round(
                    self.state["totalTaskSeconds"] + float(record.get("elapsedSeconds") or 0), 2)
                record.update({"status": "completed", "result": mark if graded else None,
                               "graded": graded, "failure": None if graded else record.get("failure")})
                if mark == "PASS":
                    self.state["passed"] += 1
                elif mark == "FAIL":
                    self.state["failed"] += 1
                else:
                    # 裁判又掉了一次：这题依旧没结论，重判不该把它说成跑完了。
                    self.state["ungraded"] = self.state.get("ungraded", 0) + 1

    def _generate_report(self, output_dir: Path) -> None:
        if not output_dir.exists() or not list(output_dir.rglob("result.json")):
            return
        self._append_log("[web] 正在生成报告…")
        report = subprocess.run(
            [sys.executable, str(ROOT / "cli.py"), "report", "--run", str(output_dir)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
        if report.returncode != 0:
            self._append_log(f"[web:error] 报告生成失败：{report.stderr.strip()}", "error")
            return
        relative_report = (output_dir / "report.md").relative_to(ROOT)
        build = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "build_web_data.py"), "--report", str(relative_report)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
        self._append_log(build.stdout.strip() or "[web] 报告已生成")
        with self.lock:
            self.state["reportReady"] = report.returncode == 0 and build.returncode == 0

    def cancel(self) -> dict:
        with self.lock:
            if self.state["status"] not in {"starting", "running", "pausing", "paused"}:
                raise RuntimeError("当前没有可取消的评测")
            self.cancel_requested = True
            self.state["status"] = "cancelling"
            process = self.process
            was_paused = self.state.get("pausedAt") is not None
            self._append_log("[web] 收到停止请求，正在终止评测…")
        if process and process.poll() is None:
            if was_paused:
                os.killpg(process.pid, signal.SIGUSR2)
            os.killpg(process.pid, signal.SIGTERM)
        return self.snapshot()

    def pause(self) -> dict:
        with self.lock:
            if self.state["status"] != "running":
                raise RuntimeError("只有运行中的评测可以暂停")
            if not self.state.get("controlReady"):
                raise RuntimeError("当前 Runner 尚未准备好安全暂停")
            process = self.process
            if process is None or process.poll() is not None:
                raise RuntimeError("评测进程不可用")
            self.state["status"] = "paused"
            self.state["pausedAt"] = time.time()
            self._append_log("[web] 已请求安全暂停；进行中的请求或工具完成后，worker 将停在下一安全点。")
        os.killpg(process.pid, signal.SIGUSR1)
        return self.snapshot()

    def resume(self) -> dict:
        with self.lock:
            if self.state["status"] != "paused":
                raise RuntimeError("当前评测没有暂停")
            process = self.process
            if process is None or process.poll() is not None:
                raise RuntimeError("评测进程不可用")
            paused_at = self.state.get("pausedAt")
            if paused_at is not None:
                self.state["pausedSeconds"] += time.time() - paused_at
            self.state["pausedAt"] = None
            self.state["status"] = "running"
            self._append_log("[web] 评测已继续。")
        os.killpg(process.pid, signal.SIGUSR2)
        return self.snapshot()


MANAGER = RunManager()
BOARD = BoardStore()


def refresh_catalog() -> None:
    """题面或判分代码变了就重新认一遍题，不用重启服务。

    不刷的话 self.tasks 一直停在启动那一刻的指纹上：改完题面刷新页面，刚跑完的结果
    会被判成 stale、排名整层看不见——"明明改了却看不到"是这类缓存最难查的 bug。
    """
    signature = catalog_signature()
    if signature == MANAGER.catalog_signature:
        return
    with MANAGER.lock:
        if signature == MANAGER.catalog_signature:
            return
        MANAGER.models, MANAGER.tasks = load_catalog()
        MANAGER.model_names = {model["name"] for model in MANAGER.models}
        MANAGER.task_ids = {task["id"] for task in MANAGER.tasks}
        MANAGER.categories = {task["category"] for task in MANAGER.tasks}
        MANAGER.catalog_signature = signature

DIFFICULTY_RANK = {"hard": 3, "medium": 2, "easy": 1, "unknown": 0}


def board_ceiling() -> int:
    """并发滑条的天花板：有 run 在跑就按那个 run 的端点，否则按默认本地档。"""
    with MANAGER.lock:
        status = MANAGER.state.get("status")
        profile_name = MANAGER.state.get("profile") if status in ACTIVE_STATUSES else None
    profile = next((model for model in MANAGER.models if model["name"] == profile_name), None)
    return (profile or {}).get("maxJobs") or LOCAL_MAX_JOBS


def active_run_task_ids(state: dict) -> list[str]:
    """当前 run **打算跑**的题目清单（按它自己的顺序）。

    为什么必须单独算这一份：`taskRuns` 里只有已经开始的题（cli 每开一题才打一行
    `[run N/M]`，服务端才记一条），所以"本轮还没轮到"的题在 taskRuns 里根本不存在。
    少了这个清单，那 40 道题就会被算进"已完成"列——一道从没跑过的题出现在已完成里，
    是看板在说谎，而不只是难看。清单优先读 run 自己的 manifest.json，读不到再按 scope 现算。
    """
    if state.get("status") not in ACTIVE_STATUSES:
        return []
    output_dir = state.get("outputDir")
    manifest = {}
    if output_dir:
        try:
            manifest = json.loads((ROOT / output_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):        # json.JSONDecodeError 是 ValueError 的子类
            manifest = {}
    tasks = [str(task) for task in (manifest.get("tasks") or []) if str(task) in MANAGER.task_ids]
    if tasks:
        return tasks
    scope = str(state.get("scope") or "")
    if scope == "all":
        return [task["id"] for task in MANAGER.tasks]
    if scope in MANAGER.categories:
        return [task["id"] for task in MANAGER.tasks if task["category"] == scope]
    return MANAGER._scope_task_ids(scope)


def board_payload() -> dict:
    """看板的单一视图 = 题面目录 + 跨行实测 + 实时泳道 + 并发实盘。

    泳道是**推导**出来的，不是存出来的，四列各说一件事：在跑 = 当前 run 正在执行的；
    本轮待跑 = 这一轮已经排上但 cli 还没开题的；已完成 = 对当前题面有有效结论的（含
    本轮刚跑完的）；排队 = 其余一切没完成的——从没跑过的、最近一次未评/被题面变更作废
    的，以及从已完成拖回去要重测的。发车后"这一轮会跑什么"由在跑+本轮待跑说清，
    "下一轮会跑什么"就是排队那一列：列里有什么，发车就原样跑什么。
    """
    history = history_payload()
    measurements = measure_by_task(history.get("runs", []))
    board = BOARD.load()
    with MANAGER.lock:
        state = dict(MANAGER.state)
    # "在跑/待跑"只在 run 进程活着时才可信：进程一结束（完成/取消/崩溃），currentTasks
    # 和 pending 条目里剩下的都是历史残留——让残骸继续占着运行泳道，卡片就永远卡死。
    active = state.get("status") in ACTIVE_STATUSES
    running = list(state.get("currentTasks") or []) if active else []
    task_runs = list(state.get("taskRuns") or []) if active else []
    pending = [item.get("taskId") for item in task_runs
               if item.get("status") == "pending" and item.get("taskId")]
    live = set(running) | {task for task in pending if task}
    live_set = {item.get("taskId"): item for item in task_runs}
    run_tasks = active_run_task_ids(state)
    started = {item.get("taskId") for item in task_runs if item.get("taskId")}
    waiting = [task for task in run_tasks if task not in started]
    waiting_set = set(waiting)
    run_position = {task: index + 1 for index, task in enumerate(run_tasks)}

    catalog = {task["id"]: task for task in MANAGER.tasks}
    ceiling = board_ceiling()
    queue = [task_id for task_id in board["queued"]
             if task_id in catalog and task_id not in live and task_id not in waiting_set]

    def has_valid_conclusion(task_id: str) -> bool:
        """已完成 = 对当前题面有结论。最近一次未评（上游故障/裁判没判成）、stale
        （题面已变更、旧结论作废）或干脆从没跑过，都不算完成——留在排队等下一轮。
        不然发车那天，四十道没跑过的题会整列挤进"已完成"，看板就是在说谎。"""
        last = (measurements.get(task_id) or {}).get("last")
        return bool(last) and not last.get("stale") and last.get("graded", True) is not False

    def lane_of(task_id: str) -> str:
        if task_id in live:
            return "running"
        if task_id in waiting_set:
            return "waiting"
        # 排队 = 显式意图 ∪ 没有有效结论。后半条保证没跑过的题不会默认落进已完成。
        if task_id in queue or not has_valid_conclusion(task_id):
            return "queue"
        return "done"

    def sort_key(task_id: str):
        """排序只在泳道内部起作用；manual 尊重拖出来的位置。"""
        position = board["order"].index(task_id) if task_id in board["order"] else len(board["order"])
        spec = catalog[task_id]
        effective = board["difficulty"].get(task_id) or spec["difficulty"]
        measure = measurements.get(task_id) or {}
        rate = measure.get("passRate")
        mode = board["sort"]
        if mode == "difficulty_desc":
            return (-DIFFICULTY_RANK.get(effective, 0), position)
        if mode == "difficulty_asc":
            return (DIFFICULTY_RANK.get(effective, 0), position)
        # 实测排序：通过率低的算难题，所以 desc = 难题在前、asc = 易题在前，与
        # difficulty_* 的方向定义一致（都是"按难度降/升序"）。没有样本的排到最后，
        # 不混进任何一端——"没测过"既不等于最难，也不等于最简单。
        group = 0 if rate is not None else 1
        hardness = 0.0 if rate is None else 1 - rate
        if mode == "measured_desc":
            return (group, -hardness, position)
        if mode == "measured_asc":
            return (group, hardness, position)
        return (position,)

    cards = []
    for task_id in catalog:
        spec = catalog[task_id]
        measure = measurements.get(task_id) or {}
        override = board["difficulty"].get(task_id)
        live_record = live_set.get(task_id)
        # 已经跑了多久：用服务端时钟算，浏览器与服务端不同钟也不至于差出一截。
        live_seconds = None
        if (live_record and live_record.get("status") == "running"
                and isinstance(live_record.get("startedAt"), (int, float))):
            live_seconds = max(0.0, round(time.time() - live_record["startedAt"], 1))
        cards.append({
            "id": task_id,
            "title": spec["title"],
            "category": spec["category"],
            "difficulty": spec["difficulty"],
            "difficultyEffective": override or spec["difficulty"],
            "difficultyOverridden": bool(override),
            "budget": spec.get("budget") or {},
            "fingerprint": spec["fingerprint"],
            "lane": lane_of(task_id),
            "measure": measure,
            "live": live_record,
            "liveSeconds": live_seconds,
            # 本轮第几题（不在本轮清单里就是 None）。卡片要能说出"它在等谁"，
            # 而不是只挤在一列笼统的"运行中"里。
            "runPosition": run_position.get(task_id),
            "runTotal": len(run_tasks) or None,
        })
    lanes = {"queue": [], "running": [], "waiting": [], "done": []}
    for card in sorted(cards, key=lambda item: sort_key(item["id"])):
        lanes[card["lane"]].append(card)
    # "本轮待跑"按 run 自己的执行顺序排，不按看板的手动/难度排序：这一列说的是
    # "接下来会发生什么"，把 cli 的排队顺序排成好看的样子就等于骗人。
    lanes["waiting"].sort(key=lambda card: (card["runPosition"] or 10 ** 9, card["id"]))
    return {
        "board": {**board, "queued": queue},
        # lanes 只给 id，卡片内容统一在 cards 里查：把同一份卡片对象再抄一遍会让
        # 一次轮询多带一倍字节，而前端要渲染顺序本来就需要一个 id 列表。
        "lanes": {name: [card["id"] for card in cards] for name, cards in lanes.items()},
        "cards": {card["id"]: card for card in cards},
        "counts": {name: len(items) for name, items in lanes.items()},
        "concurrency": MANAGER.concurrency(),
        "ceiling": ceiling,
        "run": {"id": state.get("id"), "status": state.get("status"),
                "model": state.get("model"), "profile": state.get("profile"),
                "startedJobs": state.get("jobs"), "controlReady": state.get("controlReady"),
                "paused": bool(state.get("pausedAt"))},
        "sortOptions": list(BoardStore.SORTS),
        "generatedAt": time.time(),
    }


def _run_directory(value: str | None, run_id: str) -> Path:
    candidate = Path(value) if value else ROOT / "runs" / run_id
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    candidate = candidate.resolve()
    runs_root = (ROOT / "runs").resolve()
    if runs_root in candidate.parents:
        return candidate
    safe_run_id = Path(run_id).name
    return (runs_root / safe_run_id).resolve()


def _model_directory(run_dir: Path, model: str) -> Path | None:
    """Resolve model results without allowing history metadata to escape the run."""
    candidate = (run_dir / model).resolve()
    return candidate if run_dir.resolve() in candidate.parents else None


def _failed_checks(result: dict) -> list[dict]:
    return [
        {
            "type": check.get("type", "unknown"),
            "detail": check.get("detail", ""),
            "evidence": check.get("evidence", ""),
        }
        for check in result.get("checks", [])
        if not check.get("passed")
    ]


def _run_fingerprints(run_dir: Path) -> dict[str, str]:
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return manifest.get("task_fingerprints", {})


def _run_expected_tasks(run_dir: Path) -> int:
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(manifest.get("tasks", []))


def _run_task_ids(run_dir: Path) -> list[str]:
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(manifest.get("tasks", []))


def _task_is_stale(task_id: str, metadata: dict[str, dict], fingerprints: dict[str, str]) -> bool:
    previous = fingerprints.get(task_id)
    current = metadata.get(task_id, {}).get("fingerprint")
    # Missing fingerprints are not evidence of comparability.  Older runs and
    # removed tasks must not silently enter a current-suite leaderboard.
    return not previous or not current or previous != current


def _task_results(run_dir: Path, model: str) -> list[dict]:
    metadata = {task["id"]: task for task in MANAGER.tasks}
    fingerprints = _run_fingerprints(run_dir)
    results = []
    model_dir = _model_directory(run_dir, model)
    if model_dir is None:
        return results
    for result_path in sorted(model_dir.glob("*/result.json")):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        task_id = result.get("task_id", result_path.parent.name)
        meta = metadata.get(task_id, {})
        efficiency = result.get("efficiency", {})
        judge = result.get("judge") or {}
        results.append({
            "taskId": task_id,
            "title": meta.get("title", task_id),
            "category": meta.get("category", "unknown"),
            "stale": _task_is_stale(task_id, metadata, fingerprints),
            # graded=False 表示这次尝试没有产生能力判定（上游不可用、裁判未运行或
            # 裁判档案与题目钉死的 judge_id 不符）。它既不算通过也不算失败，必须
            # 留在通过率的分母之外，否则显示层会把基础设施故障重新画成能力缺陷。
            "graded": bool(result.get("graded", True)),
            "ungradedReason": result.get("ungraded_reason"),
            "passed": bool(result.get("passed")),
            "steps": efficiency.get("steps", 0),
            "tokens": efficiency.get("tokens", 0),
            "elapsed": efficiency.get("elapsed_s", 0),
            "failure": result.get("failure_reason"),
            "judgeScore": judge.get("score"),
            "judgeVerdict": judge.get("verdict"),
            "judgeReasons": judge.get("reasons", []),
            "judgeDimensions": judge.get("dimensions", {}),
            "failedChecks": _failed_checks(result),
        })
    return results


def _graded_denominator(task_results: list[dict]) -> tuple[int, int, int]:
    """Return (passed, graded_total, ungraded) for a list of attempts.

    ``cli.py`` scores a run the same way: an ungraded attempt leaves the
    denominator instead of becoming a capability failure, so the web console
    and the markdown report can never disagree about a model's rate.
    """
    graded = [item for item in task_results if item.get("graded", True)]
    ungraded = len(task_results) - len(graded)
    passed = sum(bool(item.get("passed")) for item in graded)
    return passed, len(graded), ungraded


def _avg_success_steps(task_results: list[dict]) -> float | None:
    """Average step count over successful tasks only.

    A missing value is kept as ``None`` so the UI can distinguish a model with
    no successful tasks from a legitimate zero-step average.
    """
    successful = [item for item in task_results if item.get("passed")]
    if not successful:
        return None
    return round(sum(float(item.get("steps", 0) or 0) for item in successful) / len(successful), 1)


def history_payload() -> dict:
    """Return normalized run/model records plus an aggregate model leaderboard."""
    refresh_catalog()
    history_path = ROOT / "runs" / "_HISTORY.jsonl"
    history_entries = []
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                history_entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    records: dict[tuple[str, str], dict] = {}
    for entry in history_entries:
        for model, metrics in entry.get("models", {}).items():
            run_dir = _run_directory(entry.get("run_dir"), entry["run_id"])
            task_results = _task_results(run_dir, model)
            expected = _run_expected_tasks(run_dir)
            actual_total = len(task_results)
            graded_items = [item for item in task_results if item.get("graded", True)]
            ungraded = actual_total - len(graded_items)
            actual_passed = sum(bool(item["passed"]) for item in graded_items)
            graded_total = len(graded_items)
            n_stats = len(graded_items)
            actual_steps = round(sum(item["steps"] for item in graded_items) / n_stats, 1) if n_stats else 0
            actual_tokens = round(sum(item["tokens"] for item in graded_items) / n_stats) if n_stats else 0
            actual_elapsed = round(sum(item["elapsed"] for item in graded_items) / n_stats, 1) if n_stats else 0
            comparable = [item for item in graded_items if not item["stale"]]
            comparable_total = len(comparable)
            comparable_passed = sum(item["passed"] for item in comparable)
            # 排行榜的准入条件是"这套题的当前版本全部跑过"，与是否未评无关：
            # 一次上游故障可以把某题留在未评状态，但不该让整行模型从榜上消失。
            non_stale_total = len([item for item in task_results if not item["stale"]])
            records[(entry["run_id"], model)] = {
                "runId": entry["run_id"],
                "runDir": str(run_dir.relative_to(ROOT)),
                "timestamp": entry.get("timestamp", ""),
                "model": model,
                "status": "completed" if expected > 0 and actual_total >= expected else "interrupted",
                "passed": actual_passed,
                "total": actual_total,
                "gradedTotal": graded_total,
                "ungraded": ungraded,
                "expected": expected,
                "rate": actual_passed / graded_total if graded_total else 0,
                "avgSteps": actual_steps,
                "avgSuccessSteps": _avg_success_steps(task_results),
                "avgTokens": actual_tokens,
                "avgElapsed": actual_elapsed,
                "comparablePassed": comparable_passed,
                "comparableTotal": comparable_total,
                "nonStaleTotal": non_stale_total,
                "comparableRate": comparable_passed / comparable_total if comparable_total else 0,
                "comparableAvgTokens": round(sum(item["tokens"] for item in comparable) / comparable_total) if comparable_total else 0,
                "comparableAvgElapsed": round(sum(item["elapsed"] for item in comparable) / comparable_total, 1) if comparable_total else 0,
                "dimensions": metrics.get("dims", {}),
                "reportReady": (run_dir / "report.md").is_file(),
                "indexed": True,
                "taskResults": task_results,
            }

    for manifest_path in sorted((ROOT / "runs").glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        run_dir = manifest_path.parent
        run_id = run_dir.name
        stamp = str(manifest.get("started_at", ""))
        if re.fullmatch(r"\d{8}_\d{6}", stamp):
            stamp = datetime.strptime(stamp, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        for model in manifest.get("models", []):
            key = (run_id, model)
            if key in records:
                continue
            model_dir = _model_directory(run_dir, model)
            result_files = list(model_dir.glob("*/result.json")) if model_dir else []
            results = []
            for result_path in result_files:
                try:
                    results.append(json.loads(result_path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
            total_expected = len(manifest.get("tasks", []))
            passed = sum(bool(result.get("passed")) for result in results)
            completed = len(results)
            active = MANAGER.snapshot(since=MANAGER.log_sequence)
            if active.get("id") == run_id and active.get("status") in ACTIVE_STATUSES:
                status = active["status"]
            elif completed >= total_expected and total_expected > 0:
                status = "completed"
            else:
                status = "interrupted"
            efficiencies = [result.get("efficiency", {}) for result in results]
            successful_steps = [
                float(item.get("steps", 0) or 0)
                for item in results
                if item.get("passed")
            ]
            records[key] = {
                "runId": run_id,
                "runDir": str(run_dir.relative_to(ROOT)),
                "timestamp": stamp,
                "model": model,
                "status": status,
                "passed": passed,
                "total": completed,
                "expected": total_expected,
                "rate": passed / completed if completed else 0,
                "avgSteps": round(sum(item.get("steps", 0) for item in efficiencies) / completed, 1) if completed else 0,
                "avgSuccessSteps": round(sum(successful_steps) / len(successful_steps), 1) if successful_steps else None,
                "avgTokens": round(sum(item.get("tokens", 0) for item in efficiencies) / completed) if completed else 0,
                "avgElapsed": round(sum(item.get("elapsed_s", 0) for item in efficiencies) / completed, 1) if completed else 0,
                "dimensions": {},
                "reportReady": (run_dir / "report.md").is_file(),
                "indexed": False,
                "taskResults": [{
                    "taskId": result.get("task_id", "unknown"),
                    "title": next((task["title"] for task in MANAGER.tasks if task["id"] == result.get("task_id")), result.get("task_id", "unknown")),
                    "category": next((task["category"] for task in MANAGER.tasks if task["id"] == result.get("task_id")), "unknown"),
                    "stale": _task_is_stale(
                        result.get("task_id", "unknown"),
                        {task["id"]: task for task in MANAGER.tasks},
                        manifest.get("task_fingerprints", {}),
                    ),
                    "passed": bool(result.get("passed")),
                    # 与 _task_results 同义：未评的尝试不进通过率分母。
                    "graded": bool(result.get("graded", True)),
                    "ungradedReason": result.get("ungraded_reason"),
                    "steps": result.get("efficiency", {}).get("steps", 0),
                    "tokens": result.get("efficiency", {}).get("tokens", 0),
                    "elapsed": result.get("efficiency", {}).get("elapsed_s", 0),
                    "failure": result.get("failure_reason"),
                    "judgeScore": (result.get("judge") or {}).get("score"),
                    "judgeVerdict": (result.get("judge") or {}).get("verdict"),
                    "judgeReasons": (result.get("judge") or {}).get("reasons", []),
                    "judgeDimensions": (result.get("judge") or {}).get("dimensions", {}),
                    "failedChecks": _failed_checks(result),
                } for result in results],
            }
            # 未评的尝试既不算通过也不算失败：与 cli.py 同一口径，把这类尝试从
            # 分母和均值里摘出去，只保留 total（进度）与 ungraded（可见计数）。
            graded_items = [item for item in records[key]["taskResults"] if item["graded"]]
            passed, graded_total, ungraded = _graded_denominator(graded_items)
            n_stats = len(graded_items)
            records[key].update(
                passed=passed,
                gradedTotal=graded_total,
                ungraded=ungraded,
                rate=passed / graded_total if graded_total else 0,
                avgSteps=round(sum(item["steps"] for item in graded_items) / n_stats, 1) if n_stats else 0,
                avgTokens=round(sum(item["tokens"] for item in graded_items) / n_stats) if n_stats else 0,
                avgElapsed=round(sum(item["elapsed"] for item in graded_items) / n_stats, 1) if n_stats else 0,
            )

    run_records = sorted(records.values(), key=lambda item: (item.get("timestamp", ""), item["runId"]), reverse=True)
    for record in run_records:
        run_dir = _run_directory(record.get("runDir"), record["runId"])
        expected_ids = _run_task_ids(run_dir)
        finished_ids = {item.get("taskId") for item in record.get("taskResults", [])}
        record["missingTasks"] = [task_id for task_id in expected_ids if task_id not in finished_ids]
        record["resumable"] = record["status"] == "interrupted" and bool(record["missingTasks"])
        record["rerunnable"] = bool(expected_ids) and record["status"] in {"completed", "interrupted"}
        # 「重判」只对那些**采样完整、只差判决**的 attempt 有意义：未评原因里带 infra/config
        # 前缀的是轨迹本身没跑成，那种题只能重跑（stale 的也一样，闸门会被指纹挡下）。
        record["rejudgeable"] = [
            item.get("taskId") for item in record.get("taskResults", [])
            if not item.get("graded", True) and not item.get("stale")
            and not reason_needs_resample(item.get("ungradedReason"))
        ]
    # 一行 = 该模型最近那次对当前尺完整可比的 run，不跨 run 合并。
    # 合并会把"同一天早先那次跑坏了"的样本摊进分数：luna-6 的 401 全灭 run、
    # luna-max 被 -j4 取代的早先 run，都不该拉低各自最新一次的成绩。
    # run_records 已按时间倒序，所以"第一个进账的那次"就是最近那次。
    aggregate: dict[str, dict] = {}
    for record in run_records:
        version_total = record.get("nonStaleTotal", record.get("comparableTotal"))
        if (not record["indexed"] or record["status"] != "completed"
                or version_total != len(MANAGER.tasks)):
            continue
        if record["model"] in aggregate:
            continue
        successful = [
            item for item in record.get("taskResults", [])
            if not item.get("stale") and item.get("passed")
        ]
        comparable_total = record["comparableTotal"]
        aggregate[record["model"]] = {
            "model": record["model"], "runs": 1,
            "passed": record["comparablePassed"], "total": comparable_total,
            "tokenWeight": record["comparableAvgTokens"] * comparable_total,
            "elapsedWeight": record["comparableAvgElapsed"] * comparable_total,
            "latest": record["timestamp"], "bestRate": record["comparableRate"],
            "successStepWeight": sum(float(item.get("steps", 0) or 0) for item in successful),
            "successCount": len(successful),
            "sourceRunId": record["runId"],
        }
    leaderboard = []
    for model in aggregate.values():
        total = model.pop("total")
        model["total"] = total
        model["rate"] = model["passed"] / total if total else 0
        model["avgTokens"] = round(model.pop("tokenWeight") / total) if total else 0
        model["avgElapsed"] = round(model.pop("elapsedWeight") / total, 1) if total else 0
        success_count = model.pop("successCount")
        model["avgSuccessSteps"] = round(model.pop("successStepWeight") / success_count, 1) if success_count else None
        model["historical"] = False
        model["staleTasks"] = 0
        leaderboard.append(model)
    current_models = {item["model"] for item in leaderboard}
    # A current full-suite result must not make every older model disappear.
    # Keep one latest complete snapshot for models that have not completed the
    # current fingerprints, and mark those rows as historical/incomparable.
    latest_complete: dict[str, dict] = {}
    for record in run_records:
        if record["model"] in current_models or record["model"] in latest_complete:
            continue
        # 这里原本还要求 expected/total 等于当前题数——那等于把"整套题长大之前"的所有
        # 历史行全部挡掉：40 题时代的完整跑分一行都不剩，榜上只剩新题集那一行。
        # 历史快照本来就不与当前题集可比，正确做法是收进来并标 historical，而不是消失。
        # 但"完整"要按它自己那一版题集判：单题冒烟跑不能压着 40 题整套跑的名字排前面，
        # 所以要求 (a) 该 run 覆盖了自己 manifest 里的全部题，(b) 题数不少于当前套件的一半。
        expected = record.get("expected") or 0
        if (not record["indexed"] or record["status"] != "completed" or not record.get("total")
                or expected != record.get("total") or expected * 2 < len(MANAGER.tasks)):
            continue
        latest_complete[record["model"]] = record
    for record in latest_complete.values():
        leaderboard.append({
            "model": record["model"],
            "runs": 1,
            "passed": record["passed"],
            "total": record["total"],
            "rate": record["rate"],
            "avgTokens": record["avgTokens"],
            "avgSuccessSteps": record.get("avgSuccessSteps"),
            "avgElapsed": record["avgElapsed"],
            "latest": record["timestamp"],
            "bestRate": record["rate"],
            "historical": True,
            "staleTasks": sum(item.get("stale", False) for item in record.get("taskResults", [])),
            "sourceRunId": record["runId"],
        })
    if current_models and latest_complete:
        leaderboard_mode = "mixed"
    elif current_models:
        leaderboard_mode = "current"
    else:
        leaderboard_mode = "historical"
    # Current-config rows form the comparable ranking. Historical snapshots
    # remain visible underneath and are sorted within their own tier by rate.
    leaderboard.sort(key=lambda item: (item["historical"], -item["rate"], -item["total"], item["avgTokens"], item["model"]))
    for index, model in enumerate(leaderboard, 1):
        model["rank"] = index
    return {
        "runs": run_records,
        "leaderboard": leaderboard,
        "leaderboardMode": leaderboard_mode,
        "summary": {
            "runs": len({record["runId"] for record in run_records}),
            "executions": len(run_records),
            "completed": sum(record["status"] == "completed" for record in run_records),
            "interrupted": sum(record["status"] == "interrupted" for record in run_records),
        },
        "generatedAt": time.time(),
    }


def run_detail_payload(run_id: str) -> dict:
    active = MANAGER.snapshot()
    if active.get("id") == run_id and active.get("status") in ACTIVE_STATUSES:
        return active
    history = history_payload()
    record = next((item for item in history["runs"] if item["runId"] == run_id), None)
    if record is None:
        raise ValueError("运行记录不存在")
    run_dir = _run_directory(record.get("runDir"), run_id)
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    model = record["model"]
    runtime = (manifest.get("model_runtime") or {}).get(model, {})
    profile = profile_for_runtime(MANAGER.models, runtime) or {}
    task_order = {task_id: index for index, task_id in enumerate(manifest.get("tasks", []), 1)}
    task_runs = []
    total_tokens = total_steps = 0
    total_task_seconds = 0.0
    for result in record.get("taskResults", []):
        total_tokens += int(result.get("tokens", 0))
        total_steps += int(result.get("steps", 0))
        total_task_seconds += float(result.get("elapsed", 0))
        task_runs.append({
            "index": task_order.get(result["taskId"], 9999),
            "taskId": result["taskId"], "title": result.get("title", result["taskId"]),
            "category": result.get("category", "unknown"), "status": "completed",
            "result": ("PASS" if result.get("passed")
                       else "UNGRADED" if result.get("graded") is False else "FAIL"),
            "steps": result.get("steps", 0), "tokens": result.get("tokens", 0),
            "elapsedSeconds": result.get("elapsed", 0), "failure": result.get("failure"),
        })
    return {
        "id": run_id, "status": record["status"], "model": model,
        "profile": profile.get("name"), "apiModel": runtime.get("model"),
        "maxOutputTokens": runtime.get("max_output_tokens"),
        "requestTimeoutSeconds": runtime.get("timeout_s"), "scope": "all",
        "jobs": manifest.get("jobs"), "judge": bool(manifest.get("judge")),
        "completed": record["total"], "passed": record["passed"],
        # 未评不算失败：failed 只能在已判定的尝试里数。
        "failed": max(0, record.get("gradedTotal", record["total"]) - record["passed"]),
        "ungraded": record.get("ungraded", 0), "total": record["expected"],
        "startedAt": None, "endedAt": None, "elapsedSeconds": 0,
        "outputDir": record["runDir"], "reportReady": record["reportReady"],
        "exitCode": None, "currentTasks": [], "taskRuns": task_runs,
        "totalTokens": total_tokens, "totalSteps": total_steps,
        "totalTaskSeconds": round(total_task_seconds, 2), "controlReady": False,
        "pausedAt": None, "pausedSeconds": 0, "logs": [], "lastLogSequence": 0,
        "resumable": record.get("resumable", False), "rerunnable": record.get("rerunnable", False),
        "rejudgeable": record.get("rejudgeable", []),
        "missingTasks": record.get("missingTasks", []), "historical": True,
    }


def _trim(value, limit: int) -> str:
    """把观测结果压成可显示的字符串，并说清楚截了多少——不能悄悄改证据。"""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            value = str(value)
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n…（截断，另 {len(value) - limit} 字）"


def task_run_payload(run_id: str, task_id: str) -> dict:
    """单题单次运行的完整记录：判定明细 + 逐步轨迹。

    看板的卡片要点开得进去，而 result.json 里只有汇总，逐步过程在同目录的
    trajectory.json。两份合起来才是"这次到底做了什么"。只读，且 run_id / task_id
    都先过白名单和目录边界检查，不接受调用方拼路径。
    """
    if task_id not in MANAGER.task_ids:
        raise ValueError("未知任务")
    record = next((item for item in history_payload().get("runs", [])
                   if item.get("runId") == run_id), None)
    if record is None:
        raise ValueError("运行记录不存在")
    run_dir = _run_directory(record.get("runDir"), run_id)
    attempts = []
    for result_path in sorted(run_dir.glob(f"*/{task_id}/result.json")):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        trajectory: dict = {"steps": [], "termination": None, "error": None}
        traj_path = result_path.parent / "trajectory.json"
        if traj_path.is_file():
            try:
                raw = json.loads(traj_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            trajectory = {
                "termination": raw.get("termination"), "error": raw.get("error"),
                "totalTokens": raw.get("total_tokens"), "totalSteps": raw.get("total_steps"),
                "elapsedSeconds": raw.get("elapsed_s"), "retries": raw.get("retries"),
                "startedAt": raw.get("started_at"), "seed": raw.get("seed"),
                "steps": [
                    {
                        "idx": step.get("idx"),
                        "thought": _trim(step.get("thought", ""), 2000),
                        "toolCalls": [{"name": call.get("name"),
                                       "args": _trim(call.get("args", {}), 1200)}
                                      for call in step.get("tool_calls", [])],
                        "observations": [{"name": obs.get("name"), "ok": obs.get("ok", True),
                                          "result": _trim(obs.get("result", ""), 1600)}
                                         for obs in step.get("observations", [])],
                        "tokens": step.get("tokens"), "latencyMs": step.get("latency_ms"),
                    }
                    for step in (raw.get("steps") or [])[:80]
                ],
            }
        attempts.append({
            "model": result_path.parent.parent.name,
            "runId": run_id,
            "passed": bool(result.get("passed")),
            "graded": result.get("graded", True),
            "ungradedReason": result.get("ungraded_reason"),
            "failureReason": result.get("failure_reason"),
            "scores": result.get("scores"),
            "efficiency": result.get("efficiency"),
            "judge": result.get("judge"),
            "checks": [
                {"type": check.get("type"), "passed": bool(check.get("passed")),
                 "detail": _trim(check.get("detail", ""), 600),
                 "evidence": _trim(check.get("evidence", ""), 1600)}
                for check in (result.get("checks") or [])
            ],
            "trajectory": trajectory,
        })
    if not attempts:
        raise ValueError("这道题在该 run 里没有落盘结果")
    return {"runId": run_id, "taskId": task_id, "model": record.get("model"),
            "timestamp": record.get("timestamp"), "status": record.get("status"),
            "attempts": attempts}


def archive_model_results(model_name: str) -> dict:
    """Remove a model from active history while keeping a recoverable local archive."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", model_name):
        raise ValueError("无效模型名称")
    with MANAGER.lock:
        if MANAGER.state["status"] in ACTIVE_STATUSES:
            raise RuntimeError("评测运行中，不能删除模型数据")
        known_models = {item["model"] for item in history_payload().get("leaderboard", [])}
        if model_name not in known_models:
            raise ValueError("排名中不存在该模型")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archive_root = ROOT / "runs" / ".trash" / f"model-{model_name}-{stamp}"
        archive_root.mkdir(parents=True, exist_ok=False)
        archived_runs = []
        partial_runs = []

        for manifest_path in sorted((ROOT / "runs").glob("*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            models = list(manifest.get("models", []))
            if model_name not in models:
                continue
            run_dir = manifest_path.parent.resolve()
            if len(models) == 1:
                shutil.move(str(run_dir), str(archive_root / run_dir.name))
                archived_runs.append(run_dir.name)
                continue

            model_dir = _model_directory(run_dir, model_name)
            if model_dir and model_dir.exists():
                target = archive_root / run_dir.name
                target.mkdir(parents=True, exist_ok=True)
                shutil.move(str(model_dir), str(target / model_dir.name))
            manifest["models"] = [name for name in models if name != model_name]
            if isinstance(manifest.get("model_runtime"), dict):
                manifest["model_runtime"].pop(model_name, None)
            temporary = manifest_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, manifest_path)
            partial_runs.append(run_dir.name)

        history_path = ROOT / "runs" / "_HISTORY.jsonl"
        removed_entries = 0
        kept_lines = []
        if history_path.exists():
            for line in history_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    kept_lines.append(line)
                    continue
                models = entry.get("models", {})
                if model_name in models:
                    removed_entries += 1
                    models.pop(model_name, None)
                if models:
                    kept_lines.append(json.dumps(entry, ensure_ascii=False))
            temporary = history_path.with_suffix(".jsonl.tmp")
            temporary.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
            os.replace(temporary, history_path)

        metadata = {
            "model": model_name,
            "archivedAt": datetime.now().isoformat(timespec="seconds"),
            "archivedRuns": archived_runs,
            "partialRuns": partial_runs,
            "historyEntriesRemoved": removed_entries,
        }
        (archive_root / "archive.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {**metadata, "archive": str(archive_root.relative_to(ROOT))}


class Handler(BaseHTTPRequestHandler):
    server_version = "Tracebench/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"[web] {self.address_string()} {fmt % args}\n")

    def _json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("无效 Content-Length") from exc
        if length <= 0 or length > 65536:
            raise ValueError("请求体为空或过大")
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("请求不是有效 JSON") from exc

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self._json(MANAGER.config())
            return
        if parsed.path == "/api/runs/current":
            query = parse_qs(parsed.query)
            try:
                since = max(0, int(query.get("since", ["0"])[0]))
            except ValueError:
                since = 0
            self._json(MANAGER.snapshot(since))
            return
        events_match = re.fullmatch(r"/api/runs/(web-[A-Za-z0-9._-]+)/events", parsed.path)
        if events_match:
            query = parse_qs(parsed.query)
            cursor = int(query.get("since", ["0"])[0] or 0)
            limit = max(1, min(800, int(query.get("limit", ["400"])[0] or 400)))
            self._json(MANAGER.events_run(events_match.group(1), cursor, limit))
            return
        if parsed.path == "/api/runs/current/events":
            query = parse_qs(parsed.query)
            try:
                cursor = max(0, int(query.get("since", ["0"])[0]))
                limit = min(1000, max(1, int(query.get("limit", ["400"])[0])))
            except ValueError:
                cursor, limit = 0, 400
            self._json(MANAGER.events_since(cursor, limit))
            return
        if parsed.path == "/api/health":
            self._json({"ok": True, "service": "tracebench", "time": time.time()})
            return
        if parsed.path == "/api/history":
            self._json(history_payload())
            return
        if parsed.path == "/api/board":
            self._json(board_payload())
            return
        # 只读详情接受任意已存在的 run 目录名：cli.py 起的 run 也要能在控制台里
        # 看逐题结果。写操作（resume/rerun/cancel）仍然只认 web- 前缀，因为那才是
        # 本服务自己启动、可以安全续跑的 run。
        detail_match = re.fullmatch(r"/api/runs/([A-Za-z0-9._-]{1,80})", parsed.path)
        if detail_match:
            try:
                self._json(run_detail_payload(detail_match.group(1)))
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        report_match = re.fullmatch(r"/api/runs/([A-Za-z0-9._-]+)/report", parsed.path)
        if report_match:
            self._serve_report(report_match.group(1))
            return
        task_match = re.fullmatch(r"/api/runs/([A-Za-z0-9._-]{1,80})/tasks/([A-Za-z0-9._-]{1,80})",
                                  parsed.path)
        if task_match:
            try:
                self._json(task_run_payload(task_match.group(1), task_match.group(2)))
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/runs":
                self._json(MANAGER.start(self._read_json()), HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/board":
                request = self._read_json()
                BOARD.patch(request, MANAGER.task_ids, board_ceiling())
                self._json(board_payload())
                return
            if parsed.path == "/api/board/launch":
                request = self._read_json()
                queue = board_payload()["lanes"]["queue"]
                tasks = request.pop("tasks", None) or queue
                if not tasks:
                    raise ValueError("排队是空的：先把卡片拖进排队再发车")
                snapshot = MANAGER.start({**request, "tasks": tasks})
                # 发车即出队：这些题现在归"运行中"，跑完后要再测得重新拖一次，
                # 免得板子上留一份"已经跑过了但还写着排队"的假意图。
                BOARD.patch({"queued": []}, MANAGER.task_ids, board_ceiling())
                self._json({"run": snapshot, "launched": tasks, "board": board_payload()},
                           HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/concurrency":
                request = self._read_json()
                result = MANAGER.set_jobs(request.get("jobs"))
                self._json({"concurrency": result, "board": board_payload()})
                return
            if parsed.path == "/api/runs/current/cancel":
                self._json(MANAGER.cancel(), HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/runs/current/pause":
                self._json(MANAGER.pause(), HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/runs/current/resume":
                self._json(MANAGER.resume(), HTTPStatus.ACCEPTED)
                return
            resume_match = re.fullmatch(r"/api/runs/(web-[A-Za-z0-9._-]+)/resume", parsed.path)
            if resume_match:
                self._json(MANAGER.resume_run(resume_match.group(1)), HTTPStatus.ACCEPTED)
                return
            rerun_match = re.fullmatch(
                r"/api/runs/(web-[A-Za-z0-9._-]+)/tasks/([A-Za-z0-9._-]+)/rerun", parsed.path
            )
            if rerun_match:
                self._json(MANAGER.resume_run(rerun_match.group(1), rerun_match.group(2)), HTTPStatus.ACCEPTED)
                return
            rejudge_match = re.fullmatch(r"/api/runs/([A-Za-z0-9._-]{1,80})/rejudge", parsed.path)
            if rejudge_match:
                self._json(MANAGER.rejudge_run(rejudge_match.group(1)), HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/models/archive":
                request = self._read_json()
                self._json(archive_model_results(str(request.get("modelName", "")).strip()))
                return
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"server error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, request_path: str) -> None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_report(self, run_id: str) -> None:
        report = (ROOT / "runs" / run_id / "report.md").resolve()
        runs_root = (ROOT / "runs").resolve()
        if runs_root not in report.parents or not report.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = report.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Disposition", f'inline; filename="{run_id}-report.md"')
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tracebench local web console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()
    # 事件只活在内存里的话，刷一下页面历史轨迹就没了；启动时从磁盘回灌最近一次 run。
    loaded = MANAGER.load_events_from_disk()
    if loaded:
        print(f"Tracebench 已回灌 {loaded} 条历史事件 · run={MANAGER.state.get('id')}", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Tracebench ready at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if MANAGER.snapshot()["status"] in ACTIVE_STATUSES:
            try:
                MANAGER.cancel()
            except RuntimeError:
                pass
        server.server_close()


if __name__ == "__main__":
    main()
