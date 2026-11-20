"""Environment 基类：带状态、可重置、可断言的沙箱。"""
from __future__ import annotations
import abc
import json
import os
import shutil


class ToolResult:
    def __init__(self, ok: bool, result=None, error: str = ""):
        self.ok = ok
        self.result = result
        self.error = error

    def to_obs(self, name: str) -> dict:
        d = {"name": name, "ok": self.ok}
        if self.ok:
            d["result"] = self.result
        else:
            d["error"] = self.error
        return d


class Environment(abc.ABC):
    def __init__(self, task_dir: str, work_dir: str, snapshot: str = "default"):
        self.task_dir = task_dir      # 快照源目录
        self.work_dir = work_dir      # 运行时沙箱目录（隔离）
        self.snapshot = snapshot
        self.setup()

    # 子类按需覆盖
    def setup(self):
        """铺环境（默认铺快照目录到 work_dir）。"""
        import os
        import shutil
        os.makedirs(self.work_dir, exist_ok=True)
        shutil.copytree(self.task_dir, self.work_dir, dirs_exist_ok=True)
        self._purge_bytecode(self.work_dir)

    @staticmethod
    def _purge_bytecode(root: str) -> None:
        """删掉工作区里一切字节码与 pytest 缓存。

        两重必要性，都直接服务"可重复测试"：
        1. 快照目录里可能残留 __pycache__（本地跑测试留下的，且被 .gitignore 排除、
           也不进 task fingerprint）。CPython 只要 source/size 头对得上就复用 .pyc，
           于是一份"看起来改了"的源码可能仍按旧字节码计分——实测过：字节相同的
           store.py 因 .pyc 新旧不同，跑出 4/8 与 8/8 两种判定。
        2. 被测脚本自己写出的 .pyc 会在下一次 run 里被信任，等价于把上一轮的状态
           漏进这一轮。
        """
        for dirpath, dirnames, filenames in os.walk(root):
            if os.path.basename(dirpath) in {"__pycache__", ".pytest_cache"}:
                shutil.rmtree(dirpath, ignore_errors=True)
                dirnames[:] = []
                continue
            for fn in filenames:
                if fn.endswith((".pyc", ".pyo")):
                    try:
                        os.unlink(os.path.join(dirpath, fn))
                    except OSError:
                        pass

    @abc.abstractmethod
    def tools(self) -> list[dict]:
        """返回 OpenAI function 格式的工具 schema。"""

    @abc.abstractmethod
    def call_tool(self, name: str, args: dict) -> ToolResult:
        """执行工具，改变内部状态，返回结果。"""

    def state(self) -> dict:
        """导出终态（供断言/归因）。必须是可 json 序列化的纯数据。"""
        return {}

    def teardown(self):
        """清理。"""
