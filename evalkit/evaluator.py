"""评测编排：重置环境 -> agent loop -> 验证 -> 落盘 -> EvalResult。

判分口径全部集中在 grade()：跑新采样（run_task）和事后只重审裁判（rejudge）走同一道
闸门，两条路径不可能出现"同一份轨迹两个结论"。
"""
from __future__ import annotations
import os
import json
import shutil
import tempfile
from threading import Event
from .schema import TaskSpec, EvalResult, Efficiency, Trajectory
from .provider import ModelProvider
from .judge import judge_any_allowed
from .agent_loop import run_agent
from .verifier import run_checks
from .envs import get_env


def save_result(result: EvalResult, task_dir: str) -> str:
    path = os.path.join(task_dir, "result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, ensure_ascii=False, indent=2)
    return path


def grade(task: TaskSpec, traj: Trajectory, state: dict, model: str,
          judge=None, pause_gate: Event | None = None) -> EvalResult:
    """给定轨迹+终态算结论，不落盘。调用方决定写到哪个 attempt 目录。"""
    checks = run_checks(task, traj, state)
    # A task is complete only when the agent explicitly calls done.  Hitting a
    # budget/timeout after producing a lucky final artifact is still an
    # incomplete agent run and must not be counted as a pass.
    det_ok = traj.termination == "done" and all(c.passed for c in checks)
    ratio = (sum(c.passed for c in checks) / len(checks)) if checks else 0.0
    # Tags are catalog metadata, not independently measured abilities.  Do not
    # copy one verifier ratio into planning/recovery/state-tracking and pretend
    # those are separate measurements.
    scores = {task.category: round(ratio, 3)}

    # LLM 裁判（可选，仅当任务有 rubric 且提供了 judge）
    judge_result = None
    judge_ok = True
    judge_reason = None
    ungraded = None
    if traj.termination == "infra_error":
        # 上游连不上且重试用尽：这不是能力测量，不进分母、也不算失败。
        ungraded = "infra: " + (traj.error or "upstream unavailable")[:200]
    elif traj.termination == "config_error":
        # 端点压根不接受这次请求（鉴权/路径/model id 错）：这是我们自己的配置错，
        # 不是模型能力。曾经 50 道题一起 400 被写成 50 个 FAIL，那种数据比没有还坑。
        ungraded = "config: " + (traj.error or "endpoint rejected the request")[:200]
    # 确定性层已经判红（或压根没走到 done）时裁判翻不了绿，就不调它：以前照叫不误，
    # 于是"裁判上游挂了"能把一道已经确定失败的题踢出分母，上游故障白送通过率。
    if task.rubric and det_ok:
        if judge is None:
            # 有 rubric 却没跑裁判 = 这道题的评分标准缺了一半。以前这种情况是
            # 静默放行（实测 31 次，其中 18 次确定性层是过的），现在一律记未评。
            ungraded = "rubric 存在但本轮未启用裁判 (--judge)"
        elif (task.judge_id and getattr(judge, "identity", None) != task.judge_id
              and not judge_any_allowed()):
            # 只有严格模式（EVALKIT_JUDGE_PIN=1）下，题面钉的档案才当闸门。
            # 默认口径（2026-11-03 维护者）：尺不重要，judge_id 仅作参考。
            ungraded = (f"裁判档案不符：题面钉 {task.judge_id}，"
                        f"实跑 {getattr(judge, 'identity', '?')}")
        else:
            if pause_gate is not None:
                pause_gate.wait()
            judge_result = judge.evaluate(task, traj, state, task.rubric)
            if task.judge_id and judge_any_allowed():
                # 在理由里留一行，报告里能看出这题题面参考的是哪档裁判
                judge_result.setdefault("reasons", []).append(
                    f"[judge-pin] 题面参考 {task.judge_id}，本轮实判 {getattr(judge,'identity','?')}")
            if pause_gate is not None:
                pause_gate.wait()
            scores["judge"] = round(float(judge_result.get("score", 0) or 0), 3)
            for dimension, value in (judge_result.get("dimensions") or {}).items():
                try:
                    scores[f"rubric.{dimension}"] = round(float(value), 3)
                except (TypeError, ValueError):
                    continue
            if judge_result.get("ungraded") or judge_result.get("parse_error"):
                judge_ok = False
                ungraded = "judge: " + "; ".join(judge_result.get("reasons", []))[:200]
            elif judge_result.get("verdict") != "pass":
                judge_ok = False
                judge_reason = ("judge verdict=" + str(judge_result.get("verdict")) + ": "
                                + "; ".join(judge_result.get("reasons", [])[:2])[:160])

    passed = det_ok and judge_ok and ungraded is None

    failure = None
    if traj.termination == "infra_error":
        failure = "infra: " + (traj.error or "upstream unavailable").splitlines()[0]
    elif traj.termination == "config_error":
        failure = "config: " + (traj.error or "endpoint rejected the request").splitlines()[0]
    elif traj.termination == "error":
        failure = "error: " + (traj.error or "").splitlines()[0]
    elif traj.termination != "done":
        failed = [c for c in checks if not c.passed]
        suffix = "; " + "; ".join(f"{c.type}:{c.detail}" for c in failed[:4]) if failed else ""
        failure = f"termination={traj.termination}{suffix}"
    elif not det_ok:
        failed = [c for c in checks if not c.passed]
        failure = "; ".join(f"{c.type}:{c.detail}" for c in failed[:5])
    elif not judge_ok:
        failure = judge_reason

    result = EvalResult(
        task_id=task.id, model=model, passed=passed, scores=scores,
        graded=ungraded is None, ungraded_reason=ungraded,
        checks=checks,
        efficiency=Efficiency(steps=traj.total_steps, tokens=traj.total_tokens, elapsed_s=traj.elapsed_s),
        failure_reason=failure,
        judge=judge_result,
    )
    return result


def run_task(task: TaskSpec, provider: ModelProvider, run_dir: str, base: str = "environments",
             judge=None, pause_gate: Event | None = None) -> EvalResult:
    safe_model = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in provider.name)
    work_dir = os.path.join(tempfile.gettempdir(), "agent-eval-work", safe_model, task.id)
    shutil.rmtree(work_dir, ignore_errors=True)

    env = get_env(task.environment.name, task.environment.snapshot, work_dir, base=base)
    try:
        traj = run_agent(task, provider, env, pause_gate=pause_gate)
        if pause_gate is not None:
            pause_gate.wait()
        state = env.state()
    except Exception as e:  # noqa: BLE001
        import traceback
        try:
            state = env.state()
        except Exception:  # noqa: BLE001
            state = {}
        # 跑到这里说明是脚手架/环境自己炸了（不是模型调错工具——工具错误走
        # ToolResult(ok=False)）。这种尝试同样不是能力测量，记 infra_error。
        traj = Trajectory(
            task_id=task.id, model=provider.name, seed=provider.seed or 0,
            started_at="error", termination="infra_error",
            error=f"harness: {e}\n{traceback.format_exc()}",
        )
    finally:
        env.teardown()

    result = grade(task, traj, state, provider.name, judge=judge, pause_gate=pause_gate)

    # 落盘
    task_dir = os.path.join(run_dir, task.id)
    os.makedirs(task_dir, exist_ok=True)
    traj_path = os.path.join(task_dir, "trajectory.json")
    with open(traj_path, "w", encoding="utf-8") as f:
        json.dump(traj.model_dump(), f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(task_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
    result.trajectory_ref = traj_path
    save_result(result, task_dir)

    shutil.rmtree(work_dir, ignore_errors=True)
    return result
