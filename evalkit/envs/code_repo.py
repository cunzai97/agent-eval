"""code_repo 沙箱：铺一个代码仓，提供 读/grep/编辑/跑测试 工具。

覆盖场景：代码定位 / 改码跑测试 / 排障修复。
终态：{test_report, files_changed}
"""
from __future__ import annotations
from .sandbox import PYTEST_IMAGE, docker_run
import os
import re
import json
import subprocess
from .base import Environment, ToolResult
from .code_hidden import run_hidden_checks

_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz", ".pyc"}


def _is_text(rel: str) -> bool:
    return os.path.splitext(rel)[1].lower() not in _SKIP_EXT


class CodeRepoEnv(Environment):
    def __init__(self, task_dir, work_dir, snapshot="default"):
        self._changed: list[str] = []
        self._test_report: dict = {}
        self._answer = None
        self._files_read: list[str] = []
        self._grep_calls = 0
        self._test_runs = 0
        self._answer_calls = 0
        super().__init__(task_dir, work_dir, snapshot)

    def _abspath(self, rel: str) -> str:
        p = os.path.normpath(os.path.join(self.work_dir, rel.strip("/")))
        # 防路径逃逸
        if not p.startswith(os.path.normpath(self.work_dir)):
            raise ValueError(f"path escape: {rel}")
        return p

    def tools(self) -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": "list_files",
                "description": "列出工作区所有文件（相对路径）。可选 path 限定子目录。",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
            }},
            {"type": "function", "function": {
                "name": "read_file",
                "description": "读取文件内容。path 为相对路径。",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            }},
            {"type": "function", "function": {
                "name": "grep",
                "description": "在工作区按子串搜索，返回 文件:行号:内容。可限定 path。",
                "parameters": {"type": "object", "properties": {
                    "pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]},
            }},
            {"type": "function", "function": {
                "name": "edit_file",
                "description": "精确替换文件里的一段文本（替换第一个匹配）。old_text 必须精确存在。",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                    "required": ["path", "old_text", "new_text"]},
            }},
            {"type": "function", "function": {
                "name": "run_tests",
                "description": "运行 pytest，返回通过/失败数量和摘要。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }},
            {"type": "function", "function": {
                "name": "answer",
                "description": "提交你的最终答案（用于定位/判断类任务，如定位到的文件路径）。",
                "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            }},
        ]

    def call_tool(self, name: str, args: dict) -> ToolResult:
        try:
            if name == "list_files":
                return ToolResult(True, self._list_files(args.get("path", "")))
            if name == "read_file":
                return self._read_file(args["path"])
            if name == "grep":
                self._grep_calls += 1
                return ToolResult(True, self._grep(args["pattern"], args.get("path")))
            if name == "edit_file":
                return self._edit_file(args["path"], args["old_text"], args.get("new_text", ""))
            if name == "run_tests":
                return self._run_tests()
            if name == "answer":
                self._answer = args.get("value")
                self._answer_calls += 1
                return ToolResult(True, {"recorded": True})
            return ToolResult(False, error=f"unknown tool: {name}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=str(e))

    def _list_files(self, path: str) -> list[str]:
        base = self._abspath(path) if path else self.work_dir
        out = []
        for root, _, files in os.walk(base):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, self.work_dir)
                if _is_text(rel):
                    out.append(rel)
        return out

    def _read_file(self, path: str) -> ToolResult:
        p = self._abspath(path)
        if not os.path.isfile(p):
            return ToolResult(False, error=f"no such file: {path}")
        rel = os.path.relpath(p, self.work_dir)
        if rel not in self._files_read:
            self._files_read.append(rel)
        with open(p, encoding="utf-8", errors="replace") as f:
            content = f.read(200_000)
        return ToolResult(True, {"path": path, "content": content})

    def _grep(self, pattern: str, path: str | None) -> list[dict]:
        results = []
        # 工具说明写着"可限定 path"，但旧实现只接受文件：限定到目录时静默返回空结果，
        # 却照样计入 grep_calls。信了说明的 agent 会把有限的取证预算花在空结果上，
        # 最后得出"这个符号不存在"——这是题面与工具行为的矛盾，直接影响可解性。
        if path:
            target = self._abspath(path)
            if os.path.isdir(target):
                files = self._list_files(path)
            elif os.path.isfile(target):
                files = [path]
            else:
                raise FileNotFoundError(f"no such file or directory for grep path: {path}")
        else:
            files = self._list_files("")
        for rel in files:
            full = rel if os.path.isabs(rel) else self._abspath(rel)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if pattern in line:
                            results.append({"file": os.path.relpath(full, self.work_dir), "line": i, "text": line.rstrip()[:200]})
                            if len(results) >= 80:
                                return results
            except Exception:  # noqa: BLE001
                continue
        return results

    def _edit_file(self, path: str, old: str, new: str) -> ToolResult:
        p = self._abspath(path)
        if not os.path.isfile(p):
            return ToolResult(False, error=f"no such file: {path}")
        with open(p, encoding="utf-8") as f:
            content = f.read()
        if old not in content:
            return ToolResult(False, error="old_text not found in file (must match exactly)")
        new_content = content.replace(old, new, 1)
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_content)
        rel = os.path.relpath(p, self.work_dir)
        if rel not in self._changed:
            self._changed.append(rel)
        return ToolResult(True, {"edited": rel, "remaining_occurrences": new_content.count(new)})

    def _run_tests(self) -> ToolResult:
        self._test_runs += 1
        cmd = [
            "--rm", "--network", "none", "--read-only",
                    "-e", "PYTHONDONTWRITEBYTECODE=1",
            "--pids-limit", "96", "--memory", "384m", "--cpus", "1",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
            "--mount", f"type=bind,source={self.work_dir},target=/workspace",
            "--workdir", "/workspace", PYTEST_IMAGE,
            "python", "-m", "pytest", "tests", "-o", "addopts=", "--no-header", "-p", "no:cacheprovider",
        ]
        try:
            proc = docker_run(cmd, timeout=120, cwd=self.work_dir)
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            self._test_report = {"passed": 0, "failed": -1, "total": 0, "summary": "TIMEOUT"}
            return ToolResult(True, self._test_report)
        report = self._parse_pytest(out)
        self._test_report = report
        return ToolResult(True, report)

    @staticmethod
    def _parse_pytest(out: str) -> dict:
        passed = failed = errors = 0
        m = re.search(r"(\d+) passed", out)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", out)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+) error", out)
        if m:
            errors = int(m.group(1))
        total = passed + failed + errors
        # 抹掉 pytest 的计时尾。它必须与行尾无关：可见测试跑的不是 -q，最后一行是
        # "======== 9 passed in 0.02s ========"，早先把正则锚在 $ 上从未命中过，
        # 于是同一份解在 0.01s / 0.02s 之间产出不同的终态——只有耗时跨过取整边界时才暴露，
        # 属于"并发一上来就复现"的隐性 flake。判定用的 passed/failed/errors 计数不受影响。
        tail = "\n".join(out.strip().splitlines()[-12:])
        tail = re.sub(r"\s*\bin\s+\d+(?:\.\d+)?\s*m?s\b", " ", tail)
        return {"passed": passed, "failed": failed, "errors": errors, "total": total, "summary": tail[:2000]}

    def state(self) -> dict:
        answer_json = None
        if isinstance(self._answer, str):
            try:
                parsed = json.loads(self._answer)
                if isinstance(parsed, dict):
                    answer_json = parsed
            except json.JSONDecodeError:
                pass
        return {
            "test_report": self._test_report,
            "files_changed": self._changed,
            "answer": self._answer,
            "answer_json": answer_json,
            "files_read": self._files_read,
            "grep_calls": self._grep_calls,
            "test_runs": self._test_runs,
            "answer_calls": self._answer_calls,
            "hidden_tests": run_hidden_checks(self.snapshot, self.work_dir),
        }
