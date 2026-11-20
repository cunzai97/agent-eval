"""沙箱环境注册表：name -> 环境类。"""
from __future__ import annotations
import os
from .base import Environment, ToolResult
from .code_repo import CodeRepoEnv
from .data_ws import DataWsEnv
from .shell_ws import ShellWsEnv
from .large_file_ws import LargeFileWsEnv
from .memory_ws import MemoryCodeRepoEnv, MemoryDataWsEnv, MemoryShellWsEnv

REGISTRY = {
    "code_repo": CodeRepoEnv,
    "data_ws": DataWsEnv,
    "shell_ws": ShellWsEnv,
    "large_file_ws": LargeFileWsEnv,
    "memory_code_repo": MemoryCodeRepoEnv,
    "memory_data_ws": MemoryDataWsEnv,
    "memory_shell_ws": MemoryShellWsEnv,
}


def get_env(name: str, snapshot: str, work_dir: str, base: str = "environments") -> Environment:
    """实例化环境。快照源：base/<name>/snapshots/<snapshot>/"""
    if name not in REGISTRY:
        raise KeyError(f"unknown environment: {name} (available: {list(REGISTRY)})")
    task_dir = os.path.join(base, name, "snapshots", snapshot)
    if not os.path.isdir(task_dir):
        raise FileNotFoundError(f"snapshot not found: {task_dir}")
    return REGISTRY[name](task_dir=task_dir, work_dir=work_dir, snapshot=snapshot)
