"""核心数据模型：TaskSpec / Trajectory / EvalResult 等。"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


# ---------------- 通用 ----------------
class ToolCall(BaseModel):
    id: str
    name: str
    args: dict = Field(default_factory=dict)


class Usage(BaseModel):
    input: int = 0
    output: int = 0
    # 上游声明"这一步的 prompt 里有多少 token 来自前缀缓存"（chat 口的
    # usage.prompt_tokens_details.cached_tokens，responses 口的
    # input_tokens_details.cached_tokens）。None = 上游根本没报这个字段。
    # **只作观测，不参与判定**：第三方的缓存遥测不稳（某上游的 responses 口在同一
    # 个会话里忽报 0 / 6656 / 12800），而预算是会移动分数的东西，见
    # agent_loop._new_input_tokens。留着它是为了事后复核"这一步重读了多少"。
    cached: int | None = None


class Step(BaseModel):
    idx: int
    thought: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    observations: list[dict] = Field(default_factory=list)  # [{name, ok, result|error}]
    tokens: Usage = Field(default_factory=Usage)
    latency_ms: int = 0


class Trajectory(BaseModel):
    task_id: str
    model: str
    seed: int
    started_at: str
    steps: list[Step] = Field(default_factory=list)
    # done | max_steps | max_tokens | timeout | error | infra_error | stalled
    # infra_error = 上游此刻连不上/5xx，重试用尽；它不是模型能力的一部分，见 EvalResult.graded
    termination: str = "done"
    total_steps: int = 0
    # 预算计费口径：**相对上一步 prompt 的增量 + 本步输出**。上游的 cached 不参与，
    # 因为这条线会直接决定 max_tokens 撞不撞、进而决定 pass/fail，只有被试自己的
    # 行为能移动它（详见 agent_loop._new_input_tokens 里 2026-09-23 的记录）。
    total_tokens: int = 0
    elapsed_s: float = 0.0
    error: str | None = None
    retries: int = 0                   # 本任务里因上游故障额外发出的重试次数


# ---------------- 任务定义 ----------------
class SuccessCheck(BaseModel):
    type: Literal["state_assert", "tool_used", "no_tool", "order_of", "no_repeat_failure"]
    expr: str | None = None             # state_assert 单条件
    all_of: list[str] | None = None     # state_assert 多条件
    name: str | None = None             # tool_used / no_tool
    min_calls: int = 1                  # tool_used 最少调用次数
    tools: list[str] | None = None      # order_of 期望顺序
    max_repeat: int = 1                 # no_repeat_failure 允许的"失败后重复同参数调用"总次数


class Budget(BaseModel):
    max_steps: int = 30
    max_tokens: int = 20000
    timeout_s: int = 600


class EnvironmentSpec(BaseModel):
    name: str
    snapshot: str = "default"


class AgentSpec(BaseModel):
    system_prompt: str
    tools: list[str]


class TaskSpec(BaseModel):
    id: str
    title: str
    category: str
    tags: list[str] = Field(default_factory=list)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    environment: EnvironmentSpec
    agent: AgentSpec
    task_prompt: str
    budget: Budget = Field(default_factory=Budget)
    success: list[SuccessCheck] = Field(default_factory=list)
    rubric: str | None = None          # 可选：给 LLM 裁判的主观评分细则（开放题/规划/代码质量）
    # 有 rubric 就必须声明用哪个裁判档案打分。裁判换模型=换评分标准，
    # 这个字段进 task_fingerprint，所以换裁判会让相关题目离开同一榜单；
    # 实际跑的裁判与它不符时，该次判定记"未评"而不是照算。
    judge_id: str | None = None


# ---------------- 结果 ----------------
class CheckResult(BaseModel):
    type: str
    detail: str
    passed: bool
    evidence: str = ""


class Efficiency(BaseModel):
    steps: int = 0
    tokens: int = 0
    elapsed_s: float = 0.0


class EvalResult(BaseModel):
    task_id: str
    model: str
    passed: bool
    # graded=False：这次尝试不构成一次能力测量（上游连不上，或带 rubric 的题
    # 没评成/裁判档案不符）。分数不进分母，单独计数，绝不静默当成通过或失败。
    graded: bool = True
    ungraded_reason: str | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    checks: list[CheckResult] = Field(default_factory=list)
    efficiency: Efficiency = Field(default_factory=Efficiency)
    failure_reason: str | None = None
    judge: dict | None = None          # 可选：LLM 裁判输出
    trajectory_ref: str = ""
    # 重判留痕：这次判定没重新采样，只是拿已归档的轨迹重跑了裁判闸门（见 evalkit/rejudge.py）。
    # 记的是"裁判补判了几次"，用来区分同一条 attempt 的两次结论各自是在什么条件下得到的。
    rejudged_at: str | None = None
