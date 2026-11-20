"""Stable task fingerprints covering specs, fixtures and evaluation semantics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .schema import TaskSpec


_FRAMEWORK = (
    "evalkit/fingerprint.py",
    "evalkit/agent_loop.py",
    "evalkit/evaluator.py",
    "evalkit/verifier.py",
    "evalkit/dotify.py",
    "evalkit/provider.py",
    # 裁判的 prompt、解析口径与采样参数都会改变 pass/fail，所以它必须进指纹：
    # 换裁判实现 = 换评分标准，不能让新旧分数留在同一张榜上。
    # （"用的哪一档裁判"由题面 judge_id 携带，随 spec 一起哈希。）
    "evalkit/judge.py",
    "evalkit/envs/base.py",
)


def _add_file(digest, path: Path, root: Path) -> None:
    digest.update(str(path.relative_to(root)).encode())
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")


def task_fingerprint(task: TaskSpec, root: str | Path) -> str:
    """Hash every input that can change execution or pass/fail semantics."""
    root = Path(root).resolve()
    digest = hashlib.sha256()
    digest.update(json.dumps(task.model_dump(), ensure_ascii=False, sort_keys=True).encode())

    environment_file = root / "evalkit" / "envs" / f"{task.environment.name}.py"
    files = [root / relative for relative in _FRAMEWORK]
    files.append(environment_file)
    if task.environment.name == "code_repo":
        files.extend([
            root / "evalkit/envs/code_hidden.py",
            root / "docker/code-tests.Dockerfile",
        ])
        hidden = root / "evalkit/envs/hidden_tests" / f"{task.environment.snapshot}.py"
        if hidden.is_file():
            files.append(hidden)
    elif task.environment.name == "shell_ws":
        files.append(root / "evalkit/envs/shell_hidden.py")

    snapshot = root / "environments" / task.environment.name / "snapshots" / task.environment.snapshot
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file() or any(part in {"__pycache__", ".pytest_cache"} for part in path.parts) or path.suffix == ".pyc":
            continue
        files.append(path)

    source_config = snapshot / "source.json"
    if source_config.is_file():
        try:
            source = root / json.loads(source_config.read_text(encoding="utf-8"))["path"]
            if source.is_file():
                files.append(source)
        except (KeyError, OSError, json.JSONDecodeError, TypeError):
            pass

    seen = set()
    for path in files:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        _add_file(digest, resolved, root)
    return digest.hexdigest()[:16]
