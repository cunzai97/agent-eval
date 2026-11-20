"""Workspace environments with an explicit, auditable long-term memory loop."""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy

from .base import ToolResult
from .code_repo import CodeRepoEnv
from .data_ws import DataWsEnv
from .shell_ws import ShellWsEnv


class MemoryStoreMixin:
    """Add hidden seeded memories plus search/write/correction tools to a workspace."""

    _SEED_FILE = ".memory_seed.json"

    def __init__(self, task_dir, work_dir, snapshot="default"):
        seed_path = os.path.join(task_dir, self._SEED_FILE)
        with open(seed_path, encoding="utf-8") as source:
            seed = json.load(source)
        self._memories = deepcopy(seed.get("memories", []))
        self._seed_ids = {item["id"] for item in self._memories}
        self._memory_metrics = {"search_calls": 0, "write_calls": 0, "update_calls": 0}
        self._memory_audit: list[dict] = []
        super().__init__(task_dir, work_dir, snapshot)

        # The backing seed is evaluator state, not a workspace file that can be read directly.
        copied_seed = os.path.join(self.work_dir, self._SEED_FILE)
        if os.path.isfile(copied_seed):
            os.remove(copied_seed)
        if hasattr(self, "_snapshot_hashes"):
            self._snapshot_hashes.pop(self._SEED_FILE, None)

    @staticmethod
    def _memory_tools() -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": "memory_search",
                "description": "检索持久记忆。返回匹配的 id/key/content/tags/version；应在行动前核对相关历史结论。",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                    "required": ["query"]},
            }},
            {"type": "function", "function": {
                "name": "memory_write",
                "description": "写入新的、已验证且可复用的持久记忆；同 key 已存在时必须改用 memory_update。",
                "parameters": {"type": "object", "properties": {
                    "key": {"type": "string"}, "content": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}}},
                    "required": ["key", "content"]},
            }},
            {"type": "function", "function": {
                "name": "memory_update",
                "description": "证据推翻旧记忆时，按 id 原位修正并记录 reason；保留版本审计链。",
                "parameters": {"type": "object", "properties": {
                    "memory_id": {"type": "string"}, "content": {"type": "string"},
                    "reason": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}},
                    "required": ["memory_id", "content", "reason"]},
            }},
        ]

    def tools(self) -> list[dict]:
        return super().tools() + self._memory_tools()

    def call_tool(self, name: str, args: dict) -> ToolResult:
        try:
            if name == "memory_search":
                return self._memory_search(args)
            if name == "memory_write":
                return self._memory_write(args)
            if name == "memory_update":
                return self._memory_update(args)
            return super().call_tool(name, args)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, error=str(exc))

    def _memory_search(self, args: dict) -> ToolResult:
        self._memory_metrics["search_calls"] += 1
        query = str(args.get("query", "")).strip().casefold()
        limit = min(10, max(1, int(args.get("limit", 5))))
        terms = [term for term in re.split(r"[^\w\u4e00-\u9fff]+", query) if term]
        ranked = []
        for item in self._memories:
            if item.get("status", "active") != "active":
                continue
            haystack = " ".join([item.get("key", ""), item.get("content", ""),
                                 " ".join(item.get("tags", []))]).casefold()
            score = sum(2 if term in item.get("key", "").casefold() else 1
                        for term in terms if term in haystack)
            if not terms or score:
                ranked.append((score, item["id"], item))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        matches = [deepcopy(item) for _, _, item in ranked[:limit]]
        self._memory_audit.append({"action": "search", "query": query, "match_ids": [m["id"] for m in matches]})
        return ToolResult(True, {"query": query, "matches": matches})

    def _memory_write(self, args: dict) -> ToolResult:
        key = str(args.get("key", "")).strip()
        content = str(args.get("content", "")).strip()
        if not key or not content:
            return ToolResult(False, error="key and content must be non-empty")
        if any(item.get("key") == key and item.get("status", "active") == "active" for item in self._memories):
            return ToolResult(False, error=f"active memory key already exists: {key}; use memory_update")
        used = {item["id"] for item in self._memories}
        index = 1
        while f"mem-{index:03d}" in used:
            index += 1
        item = {"id": f"mem-{index:03d}", "key": key, "content": content,
                "tags": list(args.get("tags") or []), "status": "active", "version": 1,
                "origin": "agent"}
        self._memories.append(item)
        self._memory_metrics["write_calls"] += 1
        self._memory_audit.append({"action": "write", "memory_id": item["id"], "key": key})
        return ToolResult(True, deepcopy(item))

    def _memory_update(self, args: dict) -> ToolResult:
        memory_id = str(args.get("memory_id", "")).strip()
        content = str(args.get("content", "")).strip()
        reason = str(args.get("reason", "")).strip()
        if not content or not reason:
            return ToolResult(False, error="content and correction reason must be non-empty")
        item = next((entry for entry in self._memories if entry.get("id") == memory_id), None)
        if item is None:
            return ToolResult(False, error=f"unknown memory id: {memory_id}")
        previous = {"content": item.get("content", ""), "tags": list(item.get("tags", [])),
                    "version": int(item.get("version", 1))}
        item["content"] = content
        if "tags" in args:
            item["tags"] = list(args.get("tags") or [])
        item["version"] = previous["version"] + 1
        item["last_correction_reason"] = reason
        item["origin"] = "agent-correction"
        self._memory_metrics["update_calls"] += 1
        self._memory_audit.append({"action": "update", "memory_id": memory_id,
                                   "reason": reason, "previous": previous})
        return ToolResult(True, deepcopy(item))

    def state(self) -> dict:
        state = super().state()
        by_key = {item["key"]: deepcopy(item) for item in self._memories
                  if item.get("status", "active") == "active"}
        state["memory"] = {
            "items": deepcopy(self._memories),
            "by_key": by_key,
            "seed_ids": sorted(self._seed_ids),
            "metrics": deepcopy(self._memory_metrics),
            "audit": deepcopy(self._memory_audit),
        }
        return state


class MemoryShellWsEnv(MemoryStoreMixin, ShellWsEnv):
    pass


class MemoryDataWsEnv(MemoryStoreMixin, DataWsEnv):
    pass


class MemoryCodeRepoEnv(MemoryStoreMixin, CodeRepoEnv):
    pass
