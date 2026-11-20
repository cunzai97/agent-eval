"""Agent 执行循环：驱动被测模型跑任务，完整记录 trajectory。"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from threading import Event
from .schema import TaskSpec, Trajectory, Step
from .provider import ConfigError, InfraError, ModelProvider
from .envs.base import Environment

# 统一的结束信号工具（框架层面拦截，环境不实现）
DONE_TOOL = {
    "type": "function",
    "function": {
        "name": "done",
        "description": "任务完成后调用此工具结束。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wait_if_paused(pause_gate: Event | None) -> float:
    """Wait at a cooperative pause point and return the suspended duration."""
    if pause_gate is None or pause_gate.is_set():
        return 0.0
    started = time.monotonic()
    pause_gate.wait()
    return time.monotonic() - started


def _new_input_tokens(usage, prev_input: int) -> int:
    """本步新增的 prompt token 数：相对上一步 prompt 长度的增量。

    预算必须只由被试自己的行为移动，不能由第三方遥测移动。2026-09-22 曾把这里改成
    ``input - cached``（用上游报的前缀缓存命中量扣减），第二天就被实测打脸：某上游
    的 Responses 口对同一个会话的 cached 报得忽 0 忽 6656 忽 12800，"报了缓存"这件事
    本身不可信，于是同一个模型同一条轨迹的计费从 23,620 涨到 69,079，一行 50 题里
    6 题（5.6 那行 10 题）被 64,000 的预算线切掉——分数动了，被试什么都没变。所以
    ``cached`` 只留在 ``Step.tokens`` 里当观测数据，不参与判定。

    增量的已知偏差：前缀被驱逐、上下文重排时其实整段重读了，这里仍按增量算。那是
    一年跑批以来所有行共用的口径，宁可从一致，不可从第三方一个不稳的字段。
    """
    return max(usage.input - prev_input, 0)


def _emit(provider: ModelProvider, kind: str, **payload) -> None:
    """给控制台发一条事件（流式增量由 provider 自己发，这里发循环级的事件）。"""
    sink = getattr(provider, "on_event", None)
    if sink is None:
        return
    try:
        sink(kind, payload)
    except Exception:  # noqa: BLE001
        pass


def run_agent(task: TaskSpec, provider: ModelProvider, env: Environment,
              pause_gate: Event | None = None) -> Trajectory:
    seed = provider.seed if provider.seed is not None else 0
    traj = Trajectory(task_id=task.id, model=provider.name, seed=seed, started_at=_now_iso())
    allowed = set(task.agent.tools)
    tools_schema = [tool for tool in env.tools()
                    if tool.get("function", {}).get("name") in allowed]
    if "done" in allowed:
        tools_schema.append(DONE_TOOL)
    provider.start(task.agent.system_prompt, task.task_prompt, tools_schema)

    t0 = time.monotonic()
    paused_s = 0.0
    total_tokens = 0
    prev_input = 0          # 上一步的 prompt_tokens，用于计算 delta
    steps = 0
    text_only_streak = 0

    while steps < task.budget.max_steps:
        paused_s += _wait_if_paused(pause_gate)
        if time.monotonic() - t0 - paused_s > task.budget.timeout_s:
            traj.termination = "timeout"
            break

        t1 = time.monotonic()
        paused_before_step = paused_s
        _emit(provider, "step", idx=steps + 1, max_steps=task.budget.max_steps)
        remaining_s = task.budget.timeout_s - (t1 - t0 - paused_s)
        if remaining_s <= 0:
            traj.termination = "timeout"
            break
        try:
            resp = provider.generate(timeout_s=remaining_s)
        except InfraError as e:
            # 上游此刻不可用，而且重试预算已经用完。先给墙钟一次机会：真的超时就
            # 记 timeout（那是这一步太慢），否则记 infra_error——那既不是模型的能力，
            # 也不是模型的失误，上层必须把它挡在分数之外。
            if time.monotonic() - t0 - paused_s >= task.budget.timeout_s:
                traj.termination = "timeout"
            else:
                traj.termination = "infra_error"
                traj.error = f"upstream unavailable at step {steps}: {e}"
            traj.retries = provider.retry_stats.get("attempts", 0)
            break
        except ConfigError as e:
            # 认证/路径/model id 这类错是我们的配置问题：重试不会变，但也不是这个模型做错的。
            # 单独一类 termination，让上层记"未评"而不是扣分。
            traj.termination = "config_error"
            traj.error = f"config rejected at step {steps}: {e}"
            traj.retries = provider.retry_stats.get("attempts", 0)
            break
        except Exception as e:  # noqa: BLE001
            if time.monotonic() - t0 - paused_s >= task.budget.timeout_s:
                traj.termination = "timeout"
            else:
                traj.termination = "error"
                traj.error = f"model generate failed at step {steps}: {e}"
            traj.retries = provider.retry_stats.get("attempts", 0)
            break
        paused_s += _wait_if_paused(pause_gate)
        # 预算只按"相对上一步 prompt 的增量 + 输出"计。上游的 cached 字段进
        # Step.tokens 当观测，但不参与判定，理由见 _new_input_tokens 的注释。
        total_tokens += _new_input_tokens(resp.usage, prev_input) + resp.usage.output
        prev_input = resp.usage.input

        step = Step(idx=steps, thought=resp.content, tokens=resp.usage)
        backfill = []
        done = False
        for tc in resp.tool_calls:
            paused_s += _wait_if_paused(pause_gate)
            if tc.name == "done":
                done = True
                step.tool_calls.append(tc)
                continue
            tr = env.call_tool(tc.name, tc.args)
            obs = tr.to_obs(tc.name)
            step.observations.append(obs)
            step.tool_calls.append(tc)
            backfill.append({"id": tc.id, "output": json.dumps(obs, ensure_ascii=False, default=str)})
            _emit(provider, "tool_result", name=tc.name, ok=bool(tr.ok),
                  observation=json.dumps(obs, ensure_ascii=False, default=str)[:1200])

        paused_s += _wait_if_paused(pause_gate)
        step.latency_ms = int((time.monotonic() - t1 - (paused_s - paused_before_step)) * 1000)
        traj.steps.append(step)
        _emit(provider, "step_end", idx=steps + 1, latency_ms=step.latency_ms,
              tokens=resp.usage.output, tool_calls=len(resp.tool_calls))
        stalled = False
        if backfill:
            provider.submit_tool_results(backfill)
        elif not resp.tool_calls:
            # Some llama.cpp chat templates reject consecutive assistant turns.
            # A text-only response is not terminal in this tool-driven loop, so
            # add an explicit user continuation before asking for another turn.
            text_only_streak += 1
            if text_only_streak >= 3:
                stalled = True
            else:
                provider.request_continuation()
        else:
            text_only_streak = 0
        steps += 1

        # A terminal tool does not waive the hard resource limits.  The API can
        # only report usage after a response arrives, so enforce the budget
        # before accepting done from that response.
        if total_tokens > task.budget.max_tokens:
            traj.termination = "max_tokens"
            break
        if time.monotonic() - t0 - paused_s > task.budget.timeout_s:
            traj.termination = "timeout"
            break
        if stalled:
            traj.termination = "stalled"
            break
        if done:
            traj.termination = "done"
            break

    if traj.termination == "done":
        pass
    elif traj.termination not in ("timeout", "max_tokens", "error", "stalled", "infra_error",
                                  "config_error"):
        traj.termination = "max_steps"

    traj.total_steps = steps
    traj.total_tokens = total_tokens
    traj.elapsed_s = round(time.monotonic() - t0 - paused_s, 2)
    traj.retries = provider.retry_stats.get("attempts", 0)
    return traj
