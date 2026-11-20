"""Large-file workspace with bounded structural inspection tools.

The model may inspect metadata, search for structure, and read small line ranges.
The final processing script can stream the complete source, but large tool
observations and excessive stdout are recorded for deterministic scoring.
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile

from .base import Environment, ToolResult


def _hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_csv(path: str) -> dict:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return {"row_count": 0, "columns": [], "rows": []}
    return {"row_count": len(rows) - 1, "columns": rows[0], "rows": rows[1:]}


class LargeFileWsEnv(Environment):
    def __init__(self, task_dir, work_dir, snapshot="default"):
        self._info_calls = 0
        self._search_calls = 0
        self._range_calls = 0
        self._oversized_requests = 0
        self._preview_bytes = 0
        self._runner_stdout_bytes = 0
        self._last_run = ""
        self._snapshot_hashes: dict[str, str] = {}
        super().__init__(task_dir, work_dir, snapshot)
        with open(os.path.join(self.task_dir, "source.json"), encoding="utf-8") as f:
            config = json.load(f)
        configured_path = config["path"]
        if os.path.isabs(configured_path):
            self.source_path = configured_path
        else:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.source_path = os.path.normpath(os.path.join(project_root, configured_path))
        self.max_head_lines = int(config.get("max_head_lines", 120))
        self.max_range_lines = int(config.get("max_range_lines", 800))
        self.max_matches = int(config.get("max_matches", 40))
        self.max_preview_bytes = int(config.get("max_preview_bytes", 160_000))
        self.max_generated_bytes = int(config.get("max_generated_bytes", 1_000_000))
        if not os.path.isfile(self.source_path):
            raise FileNotFoundError(f"large source not found: {self.source_path}")
        self._source_size = os.path.getsize(self.source_path)
        self._source_mtime_ns = os.stat(self.source_path).st_mtime_ns
        self._source_hash = _hash(self.source_path)
        for root, _, files in os.walk(self.task_dir):
            for filename in files:
                path = os.path.join(root, filename)
                self._snapshot_hashes[os.path.relpath(path, self.task_dir)] = _hash(path)

    def _abspath(self, rel: str) -> str:
        path = os.path.normpath(os.path.join(self.work_dir, rel.strip("/")))
        if os.path.commonpath([self.work_dir, path]) != os.path.normpath(self.work_dir):
            raise ValueError(f"path escape: {rel}")
        return path

    def tools(self) -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": "list_files", "description": "列出工作区文件（不包含外部大文件）。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }},
            {"type": "function", "function": {
                "name": "read_file", "description": "读取工作区内的小文件，最多 200KB。不能用于外部源文件。",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            }},
            {"type": "function", "function": {
                "name": "write_file", "description": "在工作区写脚本或结果文件。",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            }},
            {"type": "function", "function": {
                "name": "source_info", "description": "返回外部大文件的大小、行数和路径；先用它判断规模，不返回正文。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }},
            {"type": "function", "function": {
                "name": "source_head", "description": f"限量查看文件开头，最多 {self.max_head_lines} 行；超限请求会被拒绝并记录。",
                "parameters": {"type": "object", "properties": {"lines": {"type": "integer"}}, "required": ["lines"]},
            }},
            {"type": "function", "function": {
                "name": "source_search", "description": f"流式搜索字面子串，只返回行号和单行摘要，最多 {self.max_matches} 条。",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}, "max_matches": {"type": "integer"}}, "required": ["query"]},
            }},
            {"type": "function", "function": {
                "name": "source_read_range", "description": f"按 1-based 行号局部读取，单次最多 {self.max_range_lines} 行。",
                "parameters": {"type": "object", "properties": {
                    "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
                    "required": ["start_line", "end_line"]},
            }},
            {"type": "function", "function": {
                "name": "run_python", "description": "运行工作区 Python 脚本处理源文件；stdout 总字节会计入评分，请只打印摘要。",
                "parameters": {"type": "object", "properties": {
                    "script": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}},
                    "required": ["script"]},
            }},
        ]

    def _bounded_preview(self, text: str) -> str:
        raw = text.encode("utf-8", errors="replace")
        remaining = max(0, self.max_preview_bytes - self._preview_bytes)
        if len(raw) > remaining:
            self._oversized_requests += 1
            raw = raw[:remaining]
        self._preview_bytes += len(raw)
        return raw.decode("utf-8", errors="replace")

    def call_tool(self, name: str, args: dict) -> ToolResult:
        try:
            if name == "list_files":
                files = [os.path.relpath(os.path.join(r, f), self.work_dir)
                         for r, _, names in os.walk(self.work_dir) for f in sorted(names)]
                return ToolResult(True, files)
            if name == "read_file":
                path = self._abspath(args["path"])
                if not os.path.isfile(path):
                    return ToolResult(False, error=f"no such workspace file: {args['path']}")
                with open(path, encoding="utf-8", errors="replace") as f:
                    return ToolResult(True, {"path": args["path"], "content": f.read(200_000)})
            if name == "write_file":
                path = self._abspath(args["path"])
                os.makedirs(os.path.dirname(path) or self.work_dir, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(args["content"])
                return ToolResult(True, {"written": args["path"], "bytes": len(args["content"].encode())})
            if name == "source_info":
                self._info_calls += 1
                with open(self.source_path, "rb") as f:
                    line_count = sum(1 for _ in f)
                return ToolResult(True, {"path": self.source_path, "bytes": self._source_size,
                                         "lines": line_count, "human_size": f"{self._source_size / 1024 / 1024:.1f} MiB"})
            if name == "source_head":
                requested = int(args["lines"])
                if requested < 1 or requested > self.max_head_lines:
                    self._oversized_requests += 1
                    return ToolResult(False, error=f"lines must be 1..{self.max_head_lines}; full-file preview is disabled")
                lines = []
                with open(self.source_path, encoding="utf-8", errors="replace") as f:
                    for _ in range(requested):
                        line = f.readline()
                        if not line:
                            break
                        lines.append(line)
                return ToolResult(True, {"start_line": 1, "content": self._bounded_preview("".join(lines))})
            if name == "source_search":
                self._search_calls += 1
                query = str(args["query"])
                limit = min(max(1, int(args.get("max_matches", self.max_matches))), self.max_matches)
                matches = []
                with open(self.source_path, encoding="utf-8", errors="replace") as f:
                    for line_number, line in enumerate(f, 1):
                        if query in line:
                            matches.append({"line": line_number, "text": line.rstrip()[:500]})
                            if len(matches) >= limit:
                                break
                self._bounded_preview(json.dumps(matches, ensure_ascii=False))
                return ToolResult(True, {"query": query, "matches": matches})
            if name == "source_read_range":
                self._range_calls += 1
                start = int(args["start_line"])
                end = int(args["end_line"])
                if start < 1 or end < start or end - start + 1 > self.max_range_lines:
                    self._oversized_requests += 1
                    return ToolResult(False, error=f"invalid/oversized range; maximum is {self.max_range_lines} lines")
                lines = []
                with open(self.source_path, encoding="utf-8", errors="replace") as f:
                    for line_number, line in enumerate(f, 1):
                        if line_number > end:
                            break
                        if line_number >= start:
                            lines.append(line)
                return ToolResult(True, {"start_line": start, "end_line": end,
                                         "content": self._bounded_preview("".join(lines))})
            if name == "run_python":
                return self._run_python(args)
            return ToolResult(False, error=f"unknown tool: {name}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, error=str(exc))

    def _run_python(self, args: dict) -> ToolResult:
        script = self._abspath(args["script"])
        if not os.path.isfile(script):
            return ToolResult(False, error=f"no such script: {args['script']}")
        cmd = [sys.executable, script] + [str(value) for value in args.get("args", [])]
        env = os.environ.copy()
        env["LARGE_SOURCE_FILE"] = self.source_path
        env["PYTHONDONTWRITEBYTECODE"] = "1"   # 见 base._purge_bytecode
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                proc = subprocess.run(cmd, cwd=self.work_dir, stdout=stdout_file, stderr=stderr_file,
                                      timeout=300, env=env)
            except subprocess.TimeoutExpired:
                return ToolResult(False, error="python timeout (300s)")
            stdout_size = stdout_file.tell()
            stderr_size = stderr_file.tell()
            self._runner_stdout_bytes += stdout_size
            stdout_file.seek(max(0, stdout_size - 4000))
            stderr_file.seek(max(0, stderr_size - 3000))
            stdout = stdout_file.read().decode("utf-8", errors="replace")
            stderr = stderr_file.read().decode("utf-8", errors="replace")
        self._last_run = stdout
        result = {"stdout_tail": stdout, "stderr_tail": stderr, "stdout_bytes": stdout_size,
                  "stderr_bytes": stderr_size, "returncode": proc.returncode}
        if proc.returncode != 0:
            return ToolResult(False, error=f"exit {proc.returncode}\n{stderr}\n{stdout}")
        return ToolResult(True, result)

    def state(self) -> dict:
        csvs = {}
        for path in glob.glob(os.path.join(self.work_dir, "**", "*.csv"), recursive=True):
            csvs[os.path.splitext(os.path.basename(path))[0]] = _parse_csv(path)
        modified = []
        for rel, digest in self._snapshot_hashes.items():
            path = os.path.join(self.work_dir, rel)
            if not os.path.isfile(path) or _hash(path) != digest:
                modified.append(rel)
        source_stat = os.stat(self.source_path)
        source_unchanged = (source_stat.st_size == self._source_size and
                            source_stat.st_mtime_ns == self._source_mtime_ns and
                            _hash(self.source_path) == self._source_hash)
        files = [os.path.relpath(os.path.join(r, f), self.work_dir)
                 for r, _, names in os.walk(self.work_dir) for f in names]
        generated_files = [rel for rel in files if rel not in self._snapshot_hashes]
        generated_bytes = sum(os.path.getsize(os.path.join(self.work_dir, rel))
                              for rel in generated_files)
        return {
            "files": files,
            "csv": csvs,
            "modified": modified,
            "source_unchanged": source_unchanged,
            "last_run": self._last_run,
            "inspection": {
                "info_calls": self._info_calls,
                "search_calls": self._search_calls,
                "range_calls": self._range_calls,
                "oversized_requests": self._oversized_requests,
                "preview_bytes": self._preview_bytes,
                "runner_stdout_bytes": self._runner_stdout_bytes,
                "generated_bytes": generated_bytes,
                "generated_file_count": len(generated_files),
                "max_generated_bytes": self.max_generated_bytes,
            },
        }
