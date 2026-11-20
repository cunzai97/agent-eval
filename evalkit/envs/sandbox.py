"""沙箱镜像常量：把"用哪个解释器跑被测代码"从浮动标签里解放出来。

可重复性要求：同一份解在任何时刻、任何机器上重放，解释器必须一致。
`python:3-slim` 这类浮动标签做不到——它今天在开发机上解析成 3.14.5，
下次 `docker pull` 就可能变成另一个 minor，而 round()/dict 序/时区数据这些
行为真的会随之变化。所以这里钉到 digest。

`agent-eval-pytest:3.12` 是本仓库自己 `docker build` 出来的镜像（见 docs/DESIGN.md），
内容由 Dockerfile 固定，标签即可复现；`python:3-slim` 是第三方镜像，只能按 digest 钉。
"""
from __future__ import annotations

import subprocess
import uuid

# code_repo 的可见/隐藏测试：本仓库构建，内容随 Dockerfile 固定
PYTEST_IMAGE = "agent-eval-pytest:3.12"

# shell_ws 的 run_bash 与 held-out 用例：第三方镜像，必须按 digest 钉
SHELL_IMAGE = "python@sha256:c845af9399020c7e562969a13689e929074a10fd057acd1b1fad06a2fb068e97"

# 人读用的解释器版本，改动 digest 时同步这里
SHELL_PYTHON_VERSION = "3.14.5"


def docker_run(args: list[str], *, timeout: float, cwd: str | None = None) -> subprocess.CompletedProcess:
    """`docker run` + 唯一容器名 + 超时自动收割，所有沙箱调用点共用。

    --rm 的自动移除依赖 docker CLI 存活：`subprocess.run` 超时会直接杀 CLI，
    容器却继续常驻（2026-10-08 孤儿容器 stupefied_perlman 就是这么烧了
    一根核 51 分钟）。这里给每次调用发一个唯一 --name，超时先对容器发
    SIGKILL 再原样抛出 TimeoutExpired，调用方照旧 `except subprocess.TimeoutExpired`。
    容器本身带 --cpus/--memory 封顶，收割失败也不会拖垮整机。
    """
    name = f"agent-eval-sbx-{uuid.uuid4().hex[:16]}"
    cmd = ["docker", "run", "--name", name, *args]
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_container(name)
        raise


def _kill_container(name: str) -> None:
    """尽力收割孤儿容器；失败不掩盖原异常。"""
    try:
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001
        pass
