"""重判：只补跑裁判闸门，不重新采样被测模型。

一道题记「未评」通常跟被测模型没关系——裁判云端抖了一下，或者那一轮压根没带
``--judge``。模型的行为已经落盘在 trajectory.json 里了，重判就是把 grade() 再走一遍，
把缺的那一半评分标准补上；旧结论先归档进 ``<run>/.attempts/``，被替换的证据不销毁。

两条边界：

- **采样本身没跑成的，重判救不回来。** ``termination`` 是 infra_error/config_error 时缺的是
  轨迹不是判决，那种 attempt 只能重跑，这里直接跳过并说明原因。
- **判定规则只有 grade() 一份。** 本模块刻意不重新实现任何 pass/fail 逻辑，也刻意
  **不进 fingerprint.py 的 ``_FRAMEWORK`` 清单**：它不参与采样，改变判分口径的改动一律
  发生在 evaluator.grade() 里，那里一改全榜指纹自然重算，不需要把重判也拖进尺子里。
"""
from __future__ import annotations
import json
import shutil
from datetime import datetime
from pathlib import Path

from .evaluator import grade, save_result
from .schema import TaskSpec, Trajectory

# 缺的是轨迹而不是判决：重判只会把同一个「未评」再确认一遍。
UNSAMPLED = {"infra_error", "config_error"}

# 同一件事的粗筛版：看板只读 result.json，不想为每个未评 attempt 回读一遍可能几 MB 的
# 轨迹，所以按 ungraded_reason 的前缀判断。真正的闸门仍然是 list_attempts 里的
# termination 检查——这里少判一个只会多显示一个按钮，多点一下会被它挡下来。
_RESAMPLE_PREFIXES = ("infra:", "config:")


def reason_needs_resample(reason: str | None) -> bool:
    text = (reason or "").strip().lower()
    return text.startswith(_RESAMPLE_PREFIXES)


def _read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_attempts(run_dir: str | Path, models=None, task_ids=None,
                  only_ungraded: bool = True) -> list[dict]:
    """扫一遍 run 目录，给出可重判的 attempt（含跳过原因，好让操作者知道为什么少了一题）。"""
    root = Path(run_dir)
    found = []
    for result_path in sorted(root.glob("*/*/result.json")):
        model = result_path.parent.parent.name
        task_id = result_path.parent.name
        if models and model not in models:
            continue
        if task_ids and task_id not in task_ids:
            continue
        try:
            previous = _read_json(result_path)
        except (OSError, json.JSONDecodeError):
            continue
        graded = bool(previous.get("graded", True))
        if only_ungraded and graded:
            continue
        termination = previous.get("termination") or ""
        if not termination:
            # result.json 不存 termination，得回读轨迹才知道这次采样成不成
            try:
                termination = _read_json(result_path.parent / "trajectory.json").get("termination") or ""
            except (OSError, json.JSONDecodeError):
                termination = "?"
        found.append({
            "model": model,
            "task_id": task_id,
            "dir": result_path.parent,
            "previous": previous,
            "termination": termination,
            "rejudgeable": termination not in UNSAMPLED and termination != "?",
        })
    return found


def rejudge_attempt(task: TaskSpec, attempt: dict, judge) -> dict:
    """对一次已归档的 attempt 重跑 grade()，写回 result.json（旧文件先归档）。"""
    directory = Path(attempt["dir"])
    try:
        traj = Trajectory.model_validate(_read_json(directory / "trajectory.json"))
        state = _read_json(directory / "state.json")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"model": attempt["model"], "task_id": attempt["task_id"],
                "ok": False, "error": f"归档轨迹不可用: {exc}"}

    previous = attempt["previous"]
    # 轨迹里记的可能是连接档案名（provider.name），而榜单认的是 run 目录那一行的 label。
    # 沿用已存的那个名字，重判不该顺手改掉这行归属。
    model_name = previous.get("model") or traj.model
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    result = grade(task, traj, state, model_name, judge=judge)
    result.rejudged_at = stamp
    result.trajectory_ref = str(directory / "trajectory.json")

    archived = None
    old_result = directory / "result.json"
    if old_result.exists():
        # <run>/.attempts/<model>/<task>/<stamp>/result.json —— 与 --merge-run 归档整目录
        # 的层级一致：run 是 directory 的上两级，多算一级就会跑到 runs/ 底下去。
        archived = directory.parent.parent / ".attempts" / attempt["model"] \
            / attempt["task_id"] / stamp / "result.json"
        archived.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_result, archived)

    save_result(result, str(directory))
    before = "PASS" if previous.get("passed") else ("未评" if not previous.get("graded", True) else "FAIL")
    after = "PASS" if result.passed else ("未评" if not result.graded else "FAIL")
    return {"model": attempt["model"], "task_id": attempt["task_id"], "ok": True,
            "before": before, "after": after, "changed": before != after,
            "graded": result.graded, "passed": result.passed,
            "judge_score": (result.judge or {}).get("score") if result.judge else None,
            "reason": result.ungraded_reason or result.failure_reason or "",
            "archived_to": str(archived) if archived else None}
