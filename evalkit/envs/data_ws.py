"""data_ws 沙箱：铺一个数据工作区，提供 读/写/跑 Python 工具。

覆盖场景：数据 pipeline（清洗/聚合/去重/join）。
终态：{outputs: {csv名: {row_count, columns, rows}}, run_output, files}
"""
from __future__ import annotations
import os
import csv
import glob
import hashlib
import subprocess
import sys
from .base import Environment, ToolResult

# 单次 read_file 的返回上限（字符）。200_000 的旧上限是一道上下文地雷：
# data-008 的 daily_bars.csv 有 440,150 字节，一次读取就回 184,397 token，
# 而网关的 ctx 只有 99,072 —— agent 做了一件最普通的事，第二步直接吃 400，
# 并且被 harness 记成"本题失败"。截断必须显式标注：不标注就是把可解的题
# 变成不可解，标注之后它只是一个"别信片段"的正常信号。
# 取值口径：12k 字符 ≈ 11k token，即使题面预算（max_tokens 64k）已经花掉大半，
# 单条观测也不足以把请求一次性推过 99k 上下文；同时它够放下 ~1000 行 CSV，
# "片段看起来很有代表性"这个陷阱本身一点没削弱。
READ_FILE_CHARS = 12_000


def _parse_csv(path: str) -> dict:
    with open(path, encoding="utf-8", newline="") as f:
        rd = list(csv.reader(f))
    if not rd:
        return {"row_count": 0, "columns": [], "rows": []}
    cols = rd[0]
    rows = rd[1:]
    return {"row_count": len(rows), "columns": cols, "rows": rows}


class DataWsEnv(Environment):
    def __init__(self, task_dir, work_dir, snapshot="default"):
        self._last_run = ""
        self._snapshot_hashes: dict[str, str] = {}
        super().__init__(task_dir, work_dir, snapshot)
        for root, _, files in os.walk(self.task_dir):
            for filename in files:
                full = os.path.join(root, filename)
                rel = os.path.relpath(full, self.task_dir)
                with open(full, "rb") as f:
                    self._snapshot_hashes[rel] = hashlib.sha256(f.read()).hexdigest()

    def _abspath(self, rel: str) -> str:
        p = os.path.normpath(os.path.join(self.work_dir, rel.strip("/")))
        if not p.startswith(os.path.normpath(self.work_dir)):
            raise ValueError(f"path escape: {rel}")
        return p

    def tools(self) -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": "list_files",
                "description": "列出工作区所有文件（相对路径）。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }},
            {"type": "function", "function": {
                "name": "read_file",
                "description": (f"读取文件内容。单次最多返回 {READ_FILE_CHARS} 字符，超出时 content 只是开头片段，"
                                "并额外给出 truncated=true 与 file_bytes（文件真实字节数）。"),
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            }},
            {"type": "function", "function": {
                "name": "write_file",
                "description": "写入文件（自动建父目录）。用于写处理脚本或结果。",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]},
            }},
            {"type": "function", "function": {
                "name": "run_python",
                "description": "运行 Python。传 script=<路径> 跑脚本文件，或传 code=<代码串> 直接执行。",
                "parameters": {"type": "object", "properties": {
                    "script": {"type": "string"}, "code": {"type": "string"}}, "required": []},
            }},
        ]

    def call_tool(self, name: str, args: dict) -> ToolResult:
        try:
            if name == "list_files":
                out = []
                for root, _, files in os.walk(self.work_dir):
                    for fn in sorted(files):
                        out.append(os.path.relpath(os.path.join(root, fn), self.work_dir))
                return ToolResult(True, out)
            if name == "read_file":
                p = self._abspath(args["path"])
                if not os.path.isfile(p):
                    return ToolResult(False, error=f"no such file: {args['path']}")
                with open(p, encoding="utf-8", errors="replace") as f:
                    content = f.read(READ_FILE_CHARS + 1)
                obs = {"path": args["path"], "content": content[:READ_FILE_CHARS],
                       "file_bytes": os.path.getsize(p)}
                if len(content) > READ_FILE_CHARS:
                    obs["truncated"] = True
                    obs["returned_chars"] = READ_FILE_CHARS
                return ToolResult(True, obs)
            if name == "write_file":
                p = self._abspath(args["path"])
                os.makedirs(os.path.dirname(p) or self.work_dir, exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(args["content"])
                return ToolResult(True, {"written": args["path"], "bytes": len(args["content"].encode())})
            if name == "run_python":
                return self._run_python(args)
            return ToolResult(False, error=f"unknown tool: {name}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=str(e))

    def _run_python(self, args: dict) -> ToolResult:
        if "script" in args:
            p = self._abspath(args["script"])
            if not os.path.isfile(p):
                return ToolResult(False, error=f"no such script: {args['script']}")
            cmd = [sys.executable, p]
        elif "code" in args:
            tmp = os.path.join(self.work_dir, "_run_tmp.py")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(args["code"])
            cmd = [sys.executable, tmp]
        else:
            return ToolResult(False, error="need 'script' or 'code'")
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"   # 见 base._purge_bytecode：字节码是判定漂移的来源
        try:
            proc = subprocess.run(cmd, cwd=self.work_dir, capture_output=True, text=True,
                                  timeout=180, env=env)
        except subprocess.TimeoutExpired:
            return ToolResult(False, error="python timeout (180s)")
        self._last_run = (proc.stdout or "")[-3000:]
        out = {"stdout": self._last_run, "stderr": (proc.stderr or "")[-2000:], "returncode": proc.returncode}
        return ToolResult(True, out) if proc.returncode == 0 else ToolResult(False, error=f"exit {proc.returncode}\n{out['stderr']}\n{out['stdout']}")

    def state(self) -> dict:
        outputs = {}
        for path in glob.glob(os.path.join(self.work_dir, "**", "*.csv"), recursive=True):
            rel = os.path.relpath(path, self.work_dir)
            name = os.path.splitext(os.path.basename(rel))[0]
            try:
                outputs[name] = _parse_csv(path)
            except Exception:  # noqa: BLE001
                outputs[name] = {"row_count": -1, "columns": [], "rows": [], "error": "parse failed"}
        modified = []
        for rel, original_hash in self._snapshot_hashes.items():
            path = os.path.join(self.work_dir, rel)
            if not os.path.isfile(path):
                modified.append(rel)
                continue
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != original_hash:
                    modified.append(rel)

        return {
            "outputs": outputs,
            "run_output": self._last_run,
            "files": [os.path.relpath(os.path.join(r, fn), self.work_dir) for r, _, fns in os.walk(self.work_dir) for fn in fns],
            "modified": modified,
        }
