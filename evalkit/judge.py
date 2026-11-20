"""LLM 裁判：只给**确定性判据结构上测不到**的部分打分。

- 用于：规划质量、开放题、代码质量、约束遵循的主观维度
- 输入：任务 + 成功标准 + rubric + 轨迹 + 环境终态
- 输出：{score(0-1), verdict, reasons[], dimensions{dim:0-1}}

两条口径是这里的全部设计约束：

1. **裁判没跑成 ≠ 通过，也 ≠ 失败。** 以前解析失败会返回 ``verdict: "fail"``，
   于是云端抖一下就算被测模型的能力问题；而未运行裁判的题被默认放行（实测 31 次
   ``judge: null``，其中 18 次确定性层是过的——等于那 18 个 PASS 少了一半评分标准）。
   现在两种情况都带 ``ungraded: True``，由 evaluator 记"未评"并从分母里剔除。
2. **同一份 rubric 必须是同一把尺。** 采样钉在 temperature 0 + 固定 seed（即使
   档案里写着 null 也覆盖掉），裁判身份 ``name:model`` 必须与题面 ``judge_id`` 一致，
   否则同样记未评。裁判的 prompt/解析代码本身进 ``task_fingerprint`` 的 `_FRAMEWORK`。
"""
from __future__ import annotations
import json
import os
import re
from .provider import ConfigError, InfraError, ModelProvider
from .schema import TaskSpec, Trajectory

JUDGE_SYSTEM = (
    "你是严格、客观的评测裁判。根据任务、成功标准、评分细则(rubric)和模型轨迹/终态打分。\n"
    "只输出一个 JSON 对象，不要任何多余文字，格式：\n"
    '{"score": <0到1的小数，总体分>, '
    '"verdict": "pass|partial|fail", '
    '"reasons": ["要点1", "要点2"], '
    '"dimensions": {"<rubric 里的维度名>": <0到1>}}\n'
    "dimensions 的 key 必须来自 rubric 里列出的维度名。"
)


# CLI 与面板用来表示"没点名，用 models.yaml 里 judge.default 那一档"的哨兵值。
JUDGE_DEFAULT = "__default__"
# 面板把"不启用裁判"发成空串/off/no/关闭 都可能，"跟随默认档"也得能表达：口径收在这两个
# 集合与下面这个函数里，别让 CLI、Web API、前端各自发明一套——那才会真的对不上。
_JUDGE_DEFAULT_CHOICES = {"default", "默认", "默认档"}
_JUDGE_OFF_CHOICES = {"off", "none", "null", "no", "false", "关闭", "不启用", "不要", "无"}


def parse_judge_choice(value) -> str | None:
    """把接口/面板发来的``judge``字段翻译成三态：None=关，哨兵=默认档，其他=档案名。

    返回 ``None`` 是"不开裁判"（带 rubric 的题会记未评）；返回 ``JUDGE_DEFAULT`` 是"开，用
    ``judge.default``那一档"，其余是档案名。两者不能合进一个假值：面板下拉框选「不启用」
    发的是 ``off``，老版勾选框未勾选时发的就是 ``false``/不发字段。
    """
    if value is None or value is False:
        return None
    if value is True:
        return JUDGE_DEFAULT
    text = str(value).strip()
    if not text:
        # 空字段当"没选裁判"：不发不等于要裁判，缺省不该默默烧 token。
        return None
    if text == JUDGE_DEFAULT or text in _JUDGE_DEFAULT_CHOICES:
        return JUDGE_DEFAULT
    lowered = text.lower()
    if lowered in _JUDGE_OFF_CHOICES or text in _JUDGE_OFF_CHOICES:
        return None
    return text


def judge_any_allowed() -> bool:
    """维护者口径（2026-11-03 更新）：**尺不重要**——题面的 judge_id 只是历史参考，
    任何裁判档案都可以判同一道题，不再因此记「未评」。

    要回到"必须同尺否则未评"的严格模式：设 `EVALKIT_JUDGE_PIN=1`
    （或 `EVALKIT_JUDGE_ANY=0`）。"""
    if (os.environ.get("EVALKIT_JUDGE_PIN") or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if (os.environ.get("EVALKIT_JUDGE_ANY") or "").strip().lower() in ("0", "false", "no", "off"):
        return False
    return True


def judge_profiles(data: dict) -> tuple[dict[str, dict], str | None]:
    """把 ``models.yaml`` 的 ``judge`` 段规范化成 ``({档案名: 配置}, 默认档案名)``。

    兼容两种写法：

    - 新：``judge: {default: mimo-flash, profiles: {mimo-flash: {...}, luna-6: {...}}}``
      —— ``profiles`` 支持 dict（键即档案名）或 list（读条目里的 ``name``）。
    - 旧：``judge: {name: ..., model: ...}`` —— 整段就是单一档案，本来也没得选。

    只规范化结构，**不碰密钥**：``${VAR}`` 引用原样留着，由 provider 在请求时展开；
    这样档案清单可以安全地发给浏览器（外流的只有"缺不缺密钥"这种事实）。
    """
    section = (data or {}).get("judge")
    profiles: dict[str, dict] = {}
    default: str | None = None
    if isinstance(section, dict):
        raw = section.get("profiles")
        items: list[tuple[str, dict]] = []
        if isinstance(raw, dict):
            items = [(str(key), value) for key, value in raw.items() if isinstance(value, dict)]
        elif isinstance(raw, list):
            items = [(str(entry.get("name") or ""), entry)
                     for entry in raw if isinstance(entry, dict)]
        for key, value in items:
            profile = dict(value)
            # 档案名以键为准：两处名字各写一份，早晚有人改掉一个，identity 从此对不上。
            profile["name"] = key
            profiles[key] = profile
        if section.get("default"):
            default = str(section["default"])
        if any(key not in {"default", "profiles"} for key in section):
            # 旧形状：整段就是一个档案。它是唯一的尺，也就当默认档。
            legacy = {key: value for key, value in section.items()
                      if key not in {"default", "profiles"}}
            name = str(legacy.get("name") or "judge")
            legacy["name"] = name
            profiles.setdefault(name, legacy)
            default = default or name
        if default is None and len(profiles) == 1:
            default = next(iter(profiles))
    return profiles, default


def resolve_judge_profile(data: dict, name: str | None = None) -> dict:
    """按档案名取一份裁判配置；``name`` 为空（或哨兵）时用 ``judge.default``。

    找不到就抛 ``ValueError`` 并列出可用档案——宁可不发车，也不要悄悄退化成另一把尺：
    分数只有同尺才可比，选错尺比不判分更难查。
    """
    profiles, default = judge_profiles(data)
    chosen = default if name in (None, "", JUDGE_DEFAULT) else str(name)
    if not profiles:
        raise ValueError("models.yaml 里没有可用的裁判档案（judge.profiles），无法启用裁判；"
                         "不要裁判就用 --no-judge")
    if not chosen:
        raise ValueError("未指定裁判档案，且 models.yaml 的 judge 段没有 default；"
                         f"可用档案：{', '.join(sorted(profiles))}")
    if chosen not in profiles:
        raise ValueError(f"未知裁判档案「{chosen}」；可用：{', '.join(sorted(profiles))}")
    return dict(profiles[chosen])


def judge_identity(cfg: dict) -> str:
    """一把尺的身份：``档案名:model``。题面 ``judge_id`` 与 manifest 记的都是它。"""
    return f"{cfg.get('name') or 'judge'}:{cfg.get('model', '?')}"


def judge_key_source(cfg: dict) -> tuple[bool, str]:
    """这把尺此刻能不能用：返回 (密钥就绪?, 来源描述)。只看环境变量在不在，不碰值。"""
    text = str(cfg.get("api_key", "") or "")
    if text.startswith("${") and text.endswith("}"):
        name = text[2:-1]
        return bool(os.environ.get(name)), f"env:{name}"
    return bool(text.strip()), "档案内联密钥"


def _traj_text(traj: Trajectory) -> str:
    lines = []
    for s in traj.steps:
        tools = [f"{tc.name}({json.dumps(tc.args, ensure_ascii=False)[:120]})" for tc in s.tool_calls]
        thought = f" | thought: {s.thought[:150]}" if s.thought else ""
        observations = []
        for observation in s.observations:
            name = observation.get("name", "tool")
            value = observation.get("result") if observation.get("ok") else observation.get("error")
            rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
            observations.append(f"{name} => {rendered[:800]}")
        evidence = f" | observations: {' || '.join(observations)}" if observations else ""
        lines.append(f"step{s.idx}: " + " | ".join(tools) + thought + evidence)
    return ("\n".join(lines)[:16000]) if lines else "(无步骤)"


def _compact_state(state: dict) -> dict:
    """Keep judge evidence useful without sending bulky workspace snapshots."""
    compact = {}
    for key in ("answer_json", "files", "modified", "files_changed", "test_report", "inspection"):
        if key in state:
            compact[key] = state[key]
    for key in ("outputs", "csv"):
        if key not in state:
            continue
        compact[key] = {}
        for name, value in state[key].items():
            if isinstance(value, dict):
                compact[key][name] = {
                    field: content for field, content in value.items()
                    if field in {"columns", "rows", "row_count"}
                }
            else:
                compact[key][name] = value
    memory = state.get("memory")
    if isinstance(memory, dict):
        compact["memory"] = {
            "metrics": memory.get("metrics", {}),
            "audit": memory.get("audit", []),
            "items": [
                {field: item.get(field) for field in (
                    "id", "key", "content", "tags", "status", "version", "origin", "last_correction_reason"
                ) if field in item}
                for item in memory.get("items", [])
            ],
        }
    return compact


def _build_prompt(task: TaskSpec, traj: Trajectory, state: dict, rubric: str) -> str:
    success = json.dumps(task.model_dump()["success"], ensure_ascii=False, indent=1)
    return f"""## 任务
{task.title}
{task.task_prompt}

## 成功标准（任务定义的确定性条件，供参考，judge 只评 rubric 维度）
{success}

## 评分细则 (rubric)
{rubric}

## 模型轨迹
{_traj_text(traj)}
终态: termination={traj.termination}, steps={traj.total_steps}, tokens={traj.total_tokens}

## 环境终态 state（已去除无关的大体积字段）
{json.dumps(_compact_state(state), ensure_ascii=False, default=str)[:12000]}

请按 rubric 的每个维度客观打分，只输出 JSON。"""


def _parse_json(raw: str):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None


class Judge:
    """一个裁判档案。``identity`` 会被写进判定，供 evaluator 与题面 ``judge_id`` 比对。"""

    def __init__(self, cfg: dict):
        pinned = dict(cfg)
        # 采样钉死：档案里写 null 也覆盖成 0，否则云端按自己的默认温度采样，
        # 同一份轨迹两次可以给出不同 verdict，而分数看起来是同一把尺。
        pinned["temperature"] = 0.0
        pinned["seed"] = cfg.get("seed") if cfg.get("seed") is not None else 20260921
        self.provider = ModelProvider(pinned)
        self.name = cfg.get("name", "judge")
        self.identity = f"{self.name}:{cfg.get('model', '?')}"

    def _ungraded(self, reason: str, raw: str = "") -> dict:
        return {"score": 0.0, "verdict": "ungraded", "ungraded": True,
                "reasons": [reason], "dimensions": {},
                "judge_model": self.name, "judge_identity": self.identity,
                "raw": raw[:500], "parse_error": True}

    def evaluate(self, task: TaskSpec, traj: Trajectory, state: dict, rubric: str) -> dict:
        if task.judge_id and task.judge_id != self.identity and not judge_any_allowed():
            # 题面钉的是另一把尺。这时 verdict 没有可比性，不能照算。
            return self._ungraded(f"裁判档案不符：题面要求 {task.judge_id}，实跑 {self.identity}")
        prompt = _build_prompt(task, traj, state, rubric)
        raw = ""
        last_err = ""
        for _ in range(3):
            try:
                raw = self.provider.chat_text(JUDGE_SYSTEM, prompt)
            except InfraError as e:
                # 裁判的上游故障：重试已经在 provider 里做过了，这里不再重试第三层，
                # 直接未评。被测模型不该为云端可用性买单。
                return self._ungraded("judge 上游不可用: " + str(e)[:200], raw)
            except ConfigError as e:
                # 鉴权/路径/model id 错：重试不会好，且它意味着我们自己的档案没配好。
                # 单独一句话点出来，别和"模型输出解析不了"混成同一条未评理由。
                return self._ungraded("judge 配置错: " + str(e)[:200], raw)
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                continue
            j = _parse_json(raw)
            if j and "score" in j:
                j["judge_model"] = self.name
                j["judge_identity"] = self.identity
                j["retries"] = self.provider.retry_stats.get("attempts", 0)
                j["raw"] = raw
                return j
            last_err = "no score in judge output"
        return self._ungraded("judge 输出不可解析: " + last_err, raw)
