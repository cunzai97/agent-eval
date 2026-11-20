"""验证器：把 TaskSpec.success 里的每个条件求值成 CheckResult。"""
from __future__ import annotations
from .schema import TaskSpec, Trajectory, CheckResult, SuccessCheck
from .dotify import eval_expr


def _tool_sequence(traj: Trajectory) -> list[str]:
    return [tc.name for step in traj.steps for tc in step.tool_calls]


def _traj_summary(traj: Trajectory) -> dict:
    return {
        "steps": traj.total_steps,
        "tokens": traj.total_tokens,
        "elapsed_s": traj.elapsed_s,
        "termination": traj.termination,
        "tool_calls": _tool_sequence(traj),
    }


def _state_assert(c: SuccessCheck, state: dict, traj: Trajectory) -> list[CheckResult]:
    exprs = [c.expr] if c.expr else (c.all_of or [])
    if not exprs:
        return [CheckResult(type="state_assert", detail="(no expr)", passed=False, evidence="missing expr")]
    out = []
    for e in exprs:
        try:
            v = eval_expr(e, state, _traj_summary(traj))
            out.append(CheckResult(type="state_assert", detail=e, passed=bool(v), evidence=f"{e} => {bool(v)}"))
        except Exception as ex:  # noqa: BLE001
            out.append(CheckResult(type="state_assert", detail=e, passed=False, evidence=f"eval error: {ex}"))
    return out


def _tool_used(c: SuccessCheck, state: dict, traj: Trajectory) -> list[CheckResult]:
    cnt = _tool_sequence(traj).count(c.name)
    return [CheckResult(
        type="tool_used",
        detail=f"{c.name} 至少调用 {c.min_calls} 次",
        passed=cnt >= c.min_calls,
        evidence=f"实际调用 {cnt} 次",
    )]


def _no_tool(c: SuccessCheck, state: dict, traj: Trajectory) -> list[CheckResult]:
    cnt = _tool_sequence(traj).count(c.name)
    return [CheckResult(type="no_tool", detail=f"never call {c.name}", passed=cnt == 0, evidence=f"called {cnt}x")]


def _order_of(c: SuccessCheck, state: dict, traj: Trajectory) -> list[CheckResult]:
    seq = _tool_sequence(traj)
    wanted = c.tools or []
    it = iter(seq)
    ok = all(any(w == x for x in it) for w in wanted)
    return [CheckResult(type="order_of", detail=f"order {wanted}", passed=ok, evidence=f"seq={seq}")]


def _repeat_failure(traj: Trajectory) -> tuple[int, list[str]]:
    """统计"同一工具+同一参数在已经失败之后被重复尝试"的总次数。

    真实观察到的失败模式：模型对同一目标连续 4 回合下达同一个落空的命令；
    以及在数据任务里反复用同一个脚本验证同一个矛盾。这类"不收敛重试"此前
    完全不计分，只是事后人工翻 trajectory 才能发现。

    刻意只认**参数完全相同**的重复，避免误伤"换个写法再试"的正常调试。
    """
    from collections import Counter
    import json

    seen: Counter = Counter()
    for step in traj.steps:
        obs_iter = iter(step.observations)
        for tc in step.tool_calls:
            if tc.name == "done":
                continue
            obs = next(obs_iter, None)
            if obs is None or obs.get("ok", True):
                continue
            try:
                key = (tc.name, json.dumps(tc.args, sort_keys=True, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                key = (tc.name, repr(tc.args))
            seen[key] += 1
    repeats = sum(n - 1 for n in seen.values() if n > 1)
    detail = [f"{name} x{n}" for (name, _), n in sorted(seen.items(), key=lambda x: -x[1]) if n > 1]
    return repeats, detail


def _no_repeat_failure(c: SuccessCheck, state: dict, traj: Trajectory) -> list[CheckResult]:
    repeats, detail = _repeat_failure(traj)
    return [CheckResult(
        type="no_repeat_failure",
        detail=f"失败后重复同参数调用不得超过 {c.max_repeat} 次",
        passed=repeats <= c.max_repeat,
        evidence=f"实际重复 {repeats} 次" + (f"; {'; '.join(detail)[:200]}" if detail else ""),
    )]


_DISPATCH = {
    "state_assert": _state_assert,
    "tool_used": _tool_used,
    "no_tool": _no_tool,
    "order_of": _order_of,
    "no_repeat_failure": _no_repeat_failure,
}


def run_checks(task: TaskSpec, traj: Trajectory, state: dict) -> list[CheckResult]:
    checks: list[CheckResult] = []
    for c in task.success:
        fn = _DISPATCH.get(c.type)
        if fn:
            checks += fn(c, state, traj)
    return checks
