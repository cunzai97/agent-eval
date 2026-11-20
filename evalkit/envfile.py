"""把仓库根的 ``.env`` 读进 ``os.environ``：密钥留在这个文件里，仓库只放引用。

为什么需要它：``models.yaml`` 里外部端点的密钥只能写成 ``"${MIMO_API_KEY}"`` 这种引用
才敢入库，可"每次开跑前先 export 一遍"是会漏的——漏了就是整轮 50 道题一起 401，
还被记成模型的分数。``.env`` 在 .gitignore 里，密钥落仓库外、不进 argv、不进 ps，
开箱就能跑。

规则故意做得最笨：
* 一行一条 ``KEY=VALUE``，``#`` 开头的行忽略；值两端的引号会被剥掉；
* **已存在的环境变量优先**——临时 ``export`` 一把能盖过文件里的旧 key，不必改文件；
* 文件不存在、读不动、某一行写坏，都只当那一行不存在，绝不因此拦住开跑。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_env_file"]


def _parse(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[7:].lstrip()
        key, sep, value = text.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: str | os.PathLike[str], override: bool = False) -> list[str]:
    """读 .env，返回这次真正写进去的变量名（供日志用，不回显值）。"""
    target = Path(path)
    if not target.is_file():
        return []
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return []
    loaded: list[str] = []
    for key, value in _parse(raw).items():
        if key in os.environ and not override:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded
