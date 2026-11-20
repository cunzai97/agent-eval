"""shell_ws 沙箱：空/预置工作区，提供 写/读/列/跑 bash 工具。

覆盖场景：脚本/工具开发（生成满足约束的脚本并真实执行验证）。
终态：{files, csv: {name:{row_count,columns,rows}}, modified: [被改的源文件], last_run}
"""
from __future__ import annotations
from .sandbox import SHELL_IMAGE, docker_run
import os
import csv
import glob
import hashlib
import shlex
import subprocess
from .base import Environment, ToolResult
from .shell_hidden import run_hidden_checks


def _parse_csv(path: str) -> dict:
    with open(path, encoding="utf-8", newline="") as f:
        rd = list(csv.reader(f))
    if not rd:
        return {"row_count": 0, "columns": [], "rows": []}
    return {"row_count": len(rd) - 1, "columns": rd[0], "rows": rd[1:]}


def _hash(p: str) -> str:
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


class ShellWsEnv(Environment):
    def __init__(self, task_dir, work_dir, snapshot="default"):
        self._last_run = ""
        self._snapshot_hashes: dict[str, str] = {}
        super().__init__(task_dir, work_dir, snapshot)
        # 记录快照里的源文件 hash，用于校验"源文件没被改"
        for r, _, fns in os.walk(self.task_dir):
            for fn in fns:
                full = os.path.join(r, fn)
                rel = os.path.relpath(full, self.task_dir)
                self._snapshot_hashes[rel] = _hash(full)

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
                "description": "读取文件内容。",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            }},
            {"type": "function", "function": {
                "name": "write_file",
                "description": "写入文件（自动建父目录）。",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]},
            }},
            {"type": "function", "function": {
                "name": "run_bash",
                "description": "在隔离工作区运行一个 Python 脚本命令（python3 <相对脚本> ...），返回 stdout/stderr。一次调用只运行一个命令。",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
            }},
        ]

    def call_tool(self, name: str, args: dict) -> ToolResult:
        try:
            if name == "list_files":
                out = []
                for r, _, fns in os.walk(self.work_dir):
                    for fn in sorted(fns):
                        out.append(os.path.relpath(os.path.join(r, fn), self.work_dir))
                return ToolResult(True, out)
            if name == "read_file":
                p = self._abspath(args["path"])
                if not os.path.isfile(p):
                    return ToolResult(False, error=f"no such file: {args['path']}")
                with open(p, encoding="utf-8", errors="replace") as f:
                    return ToolResult(True, {"path": args["path"], "content": f.read(200_000)})
            if name == "write_file":
                p = self._abspath(args["path"])
                os.makedirs(os.path.dirname(p) or self.work_dir, exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(args["content"])
                return ToolResult(True, {"written": args["path"]})
            if name == "run_bash":
                try:
                    argv = shlex.split(args["cmd"])
                except ValueError as exc:
                    return ToolResult(False, error=f"invalid command: {exc}")
                if len(argv) < 2 or os.path.basename(argv[0]) not in {"python", "python3"}:
                    return ToolResult(False, error="only 'python3 <relative-script> ...' is allowed")
                if argv[1].startswith("-"):
                    return ToolResult(False, error="inline Python and module execution are not allowed")
                for value in argv[1:]:
                    if os.path.isabs(value) or ".." in value.replace("\\", "/").split("/"):
                        return ToolResult(False, error="absolute paths and parent traversal are not allowed")
                script = self._abspath(argv[1])
                if not os.path.isfile(script):
                    return ToolResult(False, error=f"no such script: {argv[1]}")
                container_argv = [
                    "--rm", "--network", "none", "--read-only",
                    "-e", "PYTHONDONTWRITEBYTECODE=1",
                    "--pids-limit", "64", "--memory", "256m", "--cpus", "1",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                    "--mount", f"type=bind,source={self.work_dir},target=/workspace",
                    "--workdir", "/workspace", SHELL_IMAGE, "python", *argv[1:],
                ]
                try:
                    proc = docker_run(container_argv, timeout=180, cwd=self.work_dir)
                except subprocess.TimeoutExpired:
                    return ToolResult(False, error="bash timeout (180s)")
                self._last_run = (proc.stdout or "")[-3000:]
                return ToolResult(True, {"stdout": self._last_run, "stderr": (proc.stderr or "")[-2000:], "returncode": proc.returncode})
            return ToolResult(False, error=f"unknown tool: {name}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=str(e))

    def state(self) -> dict:
        csvs = {}
        for path in glob.glob(os.path.join(self.work_dir, "**", "*.csv"), recursive=True):
            name = os.path.splitext(os.path.basename(path))[0]
            try:
                csvs[name] = _parse_csv(path)
            except Exception:  # noqa: BLE001
                csvs[name] = {"row_count": -1, "columns": [], "rows": []}
        # 源文件是否被改
        modified = []
        for rel, h in self._snapshot_hashes.items():
            wp = os.path.join(self.work_dir, rel)
            if not os.path.isfile(wp) or _hash(wp) != h:
                modified.append(rel)
        files = [os.path.relpath(os.path.join(r, fn), self.work_dir) for r, _, fns in os.walk(self.work_dir) for fn in fns]
        return {
            "files": files,
            "csv": csvs,
            "modified": modified,
            "last_run": self._last_run,
            "hidden_tests": run_hidden_checks(self.snapshot, self.work_dir),
        }
