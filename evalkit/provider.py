"""模型接入：统一 OpenAI Chat Completions / Responses 协议。

两种 wire API 都支持被测 Agent 的多轮函数调用；Responses 也供 LLM judge
做纯文本评分。每个 ``ModelProvider`` 实例只承载一个任务会话。

**上游错误不是能力错误。** 一次 `run` 里 llama-server 没起来（connection refused）、
半路 5xx、连接被掐断，都只是这台机器那一刻的状态；实测 1,436 次已录尝试里 75 次是这种，
而且高度集中在个别跑批夜——一个模型的一整行分数可以被它凭空吃掉。所以这里区分三类失败：

- ``InfraError``：可重试的上游故障（连不上 / 5xx / 429 / 断连 / 读超时）。按退避重试，
  重试用尽**或**剩余时间预算不允许再试时抛出，让上层把它记成"未评"而不是"失败"。
- ``httpx.HTTPStatusError`` 的 4xx（除 429）：请求本身有问题（例如超出上下文），
  重试只是白等，直接抛出去。
- 解析/结构错误：响应格式不对，属于协议或模型缺陷，同样不重试。
"""
from __future__ import annotations
import json
import os
import time
from typing import Callable
import httpx
from .schema import ToolCall, Usage

# 这些状态码是"服务端此刻不行"，不是"这个模型不行"。
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# 非流式长生成会被网关掐断的端点（按 base_url 认，同一个进程里跑完这一轮就记住）。
# 实测小米 MiMo：非流式请求一旦真的要生成几千 token（推理模型的思考），网关在 ~5 秒处
# 直接断连（http 000，无响应体），重试 5 次都同样死；**同一个请求加 stream=true 就能
# 跑满 76 秒正常返回**。断连是"非流式专属"的，不是端点不可用——所以修法不是重试，
# 而是把传输换成流式。
_STREAM_ONLY_HOSTS: set[str] = set()


class InfraError(RuntimeError):
    """上游基础设施故障，重试用尽。调用方必须记"未评"，不得折算成任务失败。"""

    def __init__(self, message: str, *, attempts: int, cause: str):
        super().__init__(message)
        self.attempts = attempts
        self.cause = cause


class ConfigError(RuntimeError):
    """端点说"你这次请求就不对"：认证错、路径错、model id 不存在。

    与 InfraError 的区别：重试不会好（所以不重试）；与"模型做错了"的区别：这不是
    能力测量，而是我们自己的配置错——一个写错的 model id 曾把整轮 50 题写成 50 个
    FAIL 进了榜（2026-09-24），那是在给模型扣它没犯的错。
    """

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    if isinstance(exc, httpx.RequestError):
        # ConnectError / ReadTimeout / ConnectTimeout / RemoteProtocolError
        # (Server disconnected without sending a response) 都属于"这台机器此刻的状态"。
        return True
    return False


# 400 里这些字眼说明是"这次请求本身不被接受"，而不是模型能力不行：模型名错、参数不合法、
# 认证没过。上下文超长不在其中——那是 agent 自己把窗口撞爆的，必须算它的失败。
_CONFIG_HINTS = ("model not found", "unknown model", "invalid model", "no such model",
                 "unsupported model", "model does not exist", "does not exist", "invalid api key",
                 "incorrect api key", "unauthorized", "authentication")


def _check_status(response: httpx.Response) -> None:
    """``raise_for_status()`` + 把错误体一起带出来。

    流式响应一旦出了 ``with`` 块就读不到 body（httpx 的 ResponseNotRead），而"model id
    写错/认证没过"这类判断**恰恰要看 body**：只看状态码的话，400 会被当成模型自己失败，
    整轮写进榜。所以错误体在还有机会读的时候抄进异常属性里，供上层分类。
    """
    if response.status_code < 400:
        return
    try:
        body = response.read().decode("utf-8", "replace")[:800]
    except Exception:  # noqa: BLE001
        body = ""
    error = httpx.HTTPStatusError(f"HTTP {response.status_code}: {body or '(无响应体)'}",
                                  request=response.request, response=response)
    error.body_text = body
    raise error


def _error_text(exc: httpx.HTTPStatusError) -> str:
    body = getattr(exc, "body_text", "")
    if body:
        return body
    try:
        return exc.response.text
    except Exception:  # noqa: BLE001
        return ""


def _as_config_error(exc: httpx.HTTPStatusError) -> ConfigError | None:
    status = exc.response.status_code
    if status in (401, 403, 404):
        return ConfigError(
            f"端点拒绝了这个请求（HTTP {status}）：鉴权、路径或 model id 不对——"
            f"这不是模型能力，重试也不会变。响应：{_error_text(exc)[:300]}",
            status=status)
    if status == 400:
        text = _error_text(exc).lower()
        if any(hint in text for hint in _CONFIG_HINTS):
            return ConfigError(
                f"端点不接受这次请求（HTTP 400）：请求里的 model id 或参数有问题——"
                f"这不是模型能力。响应：{_error_text(exc)[:300]}",
                status=status)
    return None


def _expand_env(v):
    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
        return os.environ.get(v[2:-1], "")
    return v


def _cached_tokens(usage: dict, *details_keys: str) -> int | None:
    """取上游报告的"本步 prompt 里被前缀缓存复用的 token 数"。

    返回 ``None`` 表示这个上游根本没给缓存字段（外部 API 常见），调用方必须区分
    "没报"和"报了 0"，否则预算会把两者一样当成零命中。llama.cpp 的 chat 口放在
    ``usage.prompt_tokens_details.cached_tokens``，Responses 口放在
    ``input_tokens_details.cached_tokens``，所以按候选键依次找。
    """
    if not isinstance(usage, dict):
        return None
    for key in details_keys:
        details = usage.get(key)
        if isinstance(details, dict) and "cached_tokens" in details:
            try:
                return max(int(details.get("cached_tokens") or 0), 0)
            except (TypeError, ValueError):
                return None
    return None


class ChatResponse:
    def __init__(self, content: str, tool_calls: list[ToolCall], usage: Usage):
        self.content = content
        self.tool_calls = tool_calls
        self.usage = usage



class ModelProvider:
    def __init__(self, cfg: dict):
        self.name = cfg["name"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.api_key = _expand_env(cfg.get("api_key", "local"))
        self.wire_api = cfg.get("wire_api", "chat")
        # 推理模型 + 外部网关：profile 里写 stream: true 直接走流式；没写的在断连时自动切。
        self.stream_http = bool(cfg.get("stream", False))
        self.temperature = cfg.get("temperature", 0.0)
        self.seed = cfg.get("seed")
        self.timeout = cfg.get("timeout_s", 300)
        self.max_output_tokens = int(cfg.get("max_output_tokens", 4096))
        self.reasoning_effort = cfg.get("reasoning_effort")
        # 流式增量出口：``on_event(kind, payload)`` 在每次拿到增量时被调用，用来把
        # "模型此刻在想什么/要调什么工具"冒到控制台。它只许看，不许改流程——
        # 回调抛错不能弄丢一次已经拿到的应答（见 _emit）。
        self.on_event: Callable[[str, dict], None] | None = None
        # 重试预算：max_retries 是"额外尝试次数"，backoff 是首次等待（指数递增）。
        self.max_retries = int(cfg.get("max_retries", 4))
        self.retry_backoff_s = float(cfg.get("retry_backoff_s", 2.0))
        self.retry_stats = {"attempts": 0, "recovered": 0, "gave_up": 0}
        self._client = httpx.Client(timeout=self.timeout)
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # chat 模式状态
        self._history: list[dict] = []
        self._tools: list[dict] = []
        # Responses 模式状态。必须把模型本轮的全部 output items（包括
        # reasoning items）连同 function_call_output 一起传回后续轮次。
        self._instructions = ""
        self._responses_input: list[dict] = []
        self._responses_tools: list[dict] = []

    # ---------- 被测 Agent 会话 ----------
    def start(self, system: str, task: str, tools: list[dict]):
        if self.wire_api == "responses":
            self._instructions = system
            self._responses_input = [{"role": "user", "content": task}]
            self._responses_tools = [self._to_responses_tool(tool) for tool in (tools or [])]
            return
        self._history = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        self._tools = tools or []

    def generate(self, timeout_s: float | None = None) -> ChatResponse:
        if self.wire_api == "responses":
            return self._with_infra_retry(self._generate_responses, timeout_s, "responses")
        return self._with_infra_retry(self._generate_chat, timeout_s, "chat/completions")

    def _with_infra_retry(self, call, timeout_s: float | None, label: str):
        """在上游故障时按指数退避重试，但**绝不超过本次调用的时间预算**。

        ``timeout_s`` 由 agent_loop 传下来，含义是"这道题还剩多少墙钟"，所以重试
        只能在剩余的预算里进行；预算用完时抛出的异常会让上层按 timeout 归类
        （那是模型太慢/太长的责任），只有"预算还够、服务端就是连不上"才归 InfraError
        （那是这台机器的责任，记未评）。
        """
        deadline = time.monotonic() + (float(timeout_s) if timeout_s else float(self.timeout))
        attempt = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.retry_stats["gave_up"] += 1
                raise InfraError(f"{label}: 时间预算用尽（已尝试 {attempt + 1} 次）",
                                 attempts=attempt + 1, cause="time budget exhausted")
            try:
                result = call(min(float(self.timeout), remaining))
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, httpx.HTTPStatusError):
                    config_error = _as_config_error(exc)
                    if config_error is not None:
                        raise config_error from exc
                if not _is_retryable(exc):
                    raise
                attempt += 1
                wait_s = self.retry_backoff_s * (2 ** (attempt - 1))
                if attempt > self.max_retries or time.monotonic() + wait_s >= deadline:
                    self.retry_stats["gave_up"] += 1
                    raise InfraError(
                        f"{label}: 上游不可用，尝试 {attempt} 次后放弃 "
                        f"[{type(exc).__name__}: {str(exc)[:200]}]",
                        attempts=attempt, cause=f"{type(exc).__name__}: {exc}") from exc
                self.retry_stats["attempts"] += 1
                time.sleep(wait_s)
                continue
            if attempt:
                self.retry_stats["recovered"] += 1
            return result

    def health(self, timeout_s: float = 5.0) -> tuple[bool, str]:
        """跑批前的连通性检查。不可达就整轮别开，否则 50 道题会被同一个 refused 吃光。

        顺带（只在端点报了模型清单时）校验 ``model`` 存不存在：
        2026-09-24 有人把外部端点上的 model 留成本地占位 `local`，50 道题一起 400，
        还被记成 50 个 FAIL。发车前一次免费的 ``/models`` 就能拦住这件事。
        """
        for suffix in ("/models", "/health"):
            try:
                r = self._client.get(f"{self.base_url}{suffix}", headers=self._headers,
                                     timeout=timeout_s)
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {str(exc)[:120]}"
                continue
            if r.status_code < 500:
                listed = self._listed_models(r, suffix)
                if listed is not None and self.model not in listed:
                    # 清单里没有这个 model id：这是确定性结论，不是"网络不通"，直接抛出去——
                    # 让调用方立刻拒绝，别按不可达去等满重试窗口（一次白等 120 秒）。
                    hint = ", ".join(sorted(listed)[:6])
                    raise ConfigError(
                        f"model '{self.model}' 不在端点清单里（{suffix} 报了 {len(listed)} 个：{hint}）")
                return True, f"{suffix} -> {r.status_code}"
            last = f"{suffix} -> HTTP {r.status_code}"
        return False, last

    def _listed_models(self, response: httpx.Response, suffix: str) -> set[str] | None:
        """从端点回的"模型清单"里取出 id 集合；拿不到清单就返回 None（不校验）。"""
        if suffix != "/models":
            return None
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return None
        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return None
        ids = {str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")}
        return ids or None

    def _request_timeout(self, timeout_s: float | None) -> float:
        return (max(1.0, min(float(timeout_s), self.timeout))
                if timeout_s is not None else self.timeout)

    def _emit(self, kind: str, **payload) -> None:
        """把一条流式增量交给旁观者（控制台）。回调出错不能影响这次应答。"""
        sink = self.on_event
        if sink is None:
            return
        try:
            sink(kind, payload)
        except Exception:  # noqa: BLE001
            pass

    def _chat_body(self, stream: bool) -> dict:
        body: dict = {
            "model": self.model,
            "messages": self._history,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }
        if self.seed is not None:
            body["seed"] = self.seed
        if self._tools:
            body["tools"] = self._tools
        if stream:
            body["stream"] = True
            # 有的网关只在流末报 usage（甚至要显式要）；不要它就白丢一整笔 token 账。
            body["stream_options"] = {"include_usage": True}
        return body

    def _generate_chat(self, timeout_s: float | None = None) -> ChatResponse:
        if self.stream_http or self.base_url in _STREAM_ONLY_HOSTS:
            return self._chat_response(self._stream_chat(self._chat_body(True), timeout_s))
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions", headers=self._headers,
                json=self._chat_body(False), timeout=self._request_timeout(timeout_s),
            )
            _check_status(response)
            return self._chat_response(response.json())
        except httpx.RemoteProtocolError as exc:
            # 非流式应答被上游断在生成途中：换传输再试一次，并把这个端点记下来——
            # 同一轮里后面的题直接走流式，不再每道题都白试一次非流式。
            _STREAM_ONLY_HOSTS.add(self.base_url)
            print(f"[provider] {self.base_url} 非流式应答被断连（{type(exc).__name__}），"
                  f"改用流式重试一次（该端点在本次进程内后续直接走流式）", flush=True)
            return self._chat_response(self._stream_chat(self._chat_body(True), timeout_s))

    def _stream_chat(self, body: dict, timeout_s: float | None = None) -> dict:
        """Consume Chat Completions SSE and rebuild the non-streaming response shape.

        流式只是传输方式：拼回来的还是 ``choices[0].message`` + ``usage``，所以
        tool_call 解析与 token 记账两种传输共用同一条代码路径。
        """
        content: list[str] = []
        fragments: dict[int, dict] = {}
        usage: dict = {}
        finish_reason = None
        raw_lines: list[str] = []
        with self._client.stream(
            "POST", f"{self.base_url}/chat/completions", headers=self._headers, json=body,
            timeout=self._request_timeout(timeout_s),
        ) as response:
            _check_status(response)
            for line in response.iter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    if not line.startswith(":"):
                        raw_lines.append(line)
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(chunk.get("error"), (dict, str)):
                    raise RuntimeError("chat stream error: " + json.dumps(
                        chunk["error"], ensure_ascii=False, default=str)[:800])
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    # 推理模型的思考单独一条通道（MiMo / llama.cpp 都叫 reasoning_content），
                    # 控制台要把它和正式回答分开显示：前者是过程，后者才是产物。
                    thinking = delta.get("reasoning_content") or delta.get("reasoning")
                    if thinking:
                        self._emit("reasoning", text=thinking)
                    if delta.get("content"):
                        content.append(delta["content"])
                        self._emit("text", text=delta["content"])
                    # 工具调用的 arguments 按 index 分片下发，id/name 可能只出现在第一片里。
                    for piece in delta.get("tool_calls") or []:
                        slot = fragments.setdefault(
                            int(piece.get("index") or 0),
                            {"id": "", "name": "", "arguments": ""},
                        )
                        if piece.get("id"):
                            slot["id"] = piece["id"]
                        function = piece.get("function") or {}
                        if function.get("name"):
                            slot["name"] += function["name"]
                        if function.get("arguments"):
                            slot["arguments"] += function["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        # 认了 stream=true 却回一整段 JSON 的网关：当成普通应答解析。
        if not content and not fragments and raw_lines:
            try:
                return json.loads("\n".join(raw_lines))
            except Exception:  # noqa: BLE001
                pass
        message: dict = {"role": "assistant", "content": "".join(content)}
        if fragments:
            message["tool_calls"] = [
                {"id": slot["id"], "type": "function",
                 "function": {"name": slot["name"], "arguments": slot["arguments"]}}
                for _, slot in sorted(fragments.items())
            ]
            for _, slot in sorted(fragments.items()):
                self._emit("tool_call", name=slot["name"], arguments=slot["arguments"])
        return {"choices": [{"message": message, "finish_reason": finish_reason}], "usage": usage}

    def _chat_response(self, data: dict) -> ChatResponse:
        """非流式 JSON 与流式拼装结果共用的解析：tool_calls 归一化 + assistant 消息回填。"""
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        content = msg.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args_raw = fn.get("arguments", "{}")
            try:
                if isinstance(args_raw, dict):
                    args = args_raw
                else:
                    args = json.loads(args_raw) if args_raw else {}
            except Exception:
                args = {"_raw": args_raw}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id") or f"call_{len(tool_calls)}",
                    name=fn.get("name", ""),
                    args=args,
                )
            )
        u = data.get("usage", {})
        usage = Usage(input=u.get("prompt_tokens", 0), output=u.get("completion_tokens", 0),
                      cached=_cached_tokens(u, "prompt_tokens_details", "input_tokens_details"))
        # 记录 assistant 消息（tool_calls 规范化，arguments 转回 json 字符串）
        self._history.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.args, ensure_ascii=False)},
                }
                for tc in tool_calls
            ] or None,
        })
        return ChatResponse(content, tool_calls, usage)

    @staticmethod
    def _to_responses_tool(tool: dict) -> dict:
        """Convert a Chat Completions function tool to Responses shape."""
        if tool.get("type") != "function":
            return dict(tool)
        fn = tool.get("function", {})
        converted = {
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        }
        if "strict" in fn:
            converted["strict"] = fn["strict"]
        return converted

    def _responses_body(self, input_items: list[dict], tools: list[dict] | None = None,
                        instructions: str = "") -> dict:
        body: dict = {
            "model": self.model,
            "input": input_items,
            "stream": True,
            "max_output_tokens": self.max_output_tokens,
        }
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = tools
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
        return body

    def _stream_responses(self, body: dict, timeout_s: float | None = None) -> dict:
        """Consume Responses SSE and return the final response object.

        ``response.completed`` normally contains the canonical full response.
        The output-item fallback keeps compatible proxies usable when they omit it.
        """
        response: dict | None = None
        completed_items: list[tuple[int, dict]] = []
        text_deltas: list[str] = []
        raw_lines: list[str] = []
        with self._client.stream(
            "POST", f"{self.base_url}/responses", headers=self._headers, json=body,
            timeout=self._request_timeout(timeout_s),
        ) as r:
            _check_status(r)
            for line in r.iter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    if not line.startswith("event:"):
                        raw_lines.append(line)
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    event = json.loads(payload)
                except Exception:  # noqa: BLE001
                    continue
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    text_deltas.append(event.get("delta", ""))
                    self._emit("text", text=event.get("delta", ""))
                elif event_type in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
                    # 思考流：Responses 口可能叫 reasoning_text，也可能是 summary，两个都收。
                    self._emit("reasoning", text=event.get("delta", ""))
                elif event_type == "response.output_item.done" and isinstance(event.get("item"), dict):
                    completed_items.append((int(event.get("output_index", len(completed_items))), event["item"]))
                    item = event["item"]
                    if item.get("type") == "function_call":
                        self._emit("tool_call", name=item.get("name", ""),
                                   arguments=item.get("arguments", ""))
                elif event_type in ("response.completed", "response.incomplete"):
                    if isinstance(event.get("response"), dict):
                        response = event["response"]
                elif event_type == "response.failed":
                    failed = event.get("response") or {}
                    raise RuntimeError("responses API failed: " + json.dumps(
                        failed.get("error") or failed, ensure_ascii=False, default=str)[:800])
                elif event_type == "error":
                    raise RuntimeError("responses API stream error: " + json.dumps(
                        event.get("error") or event, ensure_ascii=False, default=str)[:800])

        # A few compatible gateways honor stream=true but return one JSON line.
        if response is None and raw_lines:
            try:
                parsed = json.loads("\n".join(raw_lines))
                response = parsed.get("response", parsed)
            except Exception:  # noqa: BLE001
                pass
        if response is None:
            response = {
                "output": [item for _, item in sorted(completed_items, key=lambda pair: pair[0])],
                "usage": {},
            }
        # Some gateways emit correct output_item.done events but leave the
        # response.completed.output array empty. Prefer those canonical done
        # items over synthesizing a text-only message from deltas.
        if not response.get("output") and completed_items:
            response["output"] = [
                item for _, item in sorted(completed_items, key=lambda pair: pair[0])
            ]
        if not response.get("output") and text_deltas:
            response["output"] = [{
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "".join(text_deltas)}],
            }]
        return response

    @staticmethod
    def _responses_text(output: list[dict]) -> str:
        parts: list[str] = []
        for item in output:
            if item.get("type") != "message":
                continue
            content = item.get("content", [])
            if isinstance(content, str):
                parts.append(content)
                continue
            for part in content or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    parts.append(part.get("text", ""))
        return "".join(parts)

    def _generate_responses(self, timeout_s: float | None = None) -> ChatResponse:
        body = self._responses_body(
            self._responses_input, self._responses_tools, self._instructions,
        )
        data = self._stream_responses(body, timeout_s)
        output = data.get("output") or []
        # Preserve every output item for reasoning/tool continuity.
        self._responses_input.extend(output)

        tool_calls: list[ToolCall] = []
        for item in output:
            if item.get("type") != "function_call":
                continue
            args_raw = item.get("arguments", "{}")
            try:
                args = args_raw if isinstance(args_raw, dict) else json.loads(args_raw or "{}")
            except Exception:  # noqa: BLE001
                args = {"_raw": args_raw}
            tool_calls.append(ToolCall(
                id=item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}",
                name=item.get("name", ""), args=args,
            ))
        raw_usage = data.get("usage") or {}
        usage = Usage(
            input=raw_usage.get("input_tokens", raw_usage.get("prompt_tokens", 0)) or 0,
            output=raw_usage.get("output_tokens", raw_usage.get("completion_tokens", 0)) or 0,
            cached=_cached_tokens(raw_usage, "input_tokens_details", "prompt_tokens_details"),
        )
        return ChatResponse(self._responses_text(output), tool_calls, usage)

    def submit_tool_results(self, results: list[dict]):
        """results: [{id, output(str)}]，回填工具结果到对话历史。"""
        if self.wire_api == "responses":
            for res in results:
                self._responses_input.append({
                    "type": "function_call_output",
                    "call_id": res["id"],
                    "output": res["output"],
                })
            return
        for res in results:
            self._history.append({
                "role": "tool",
                "tool_call_id": res["id"],
                "content": res["output"],
            })

    def request_continuation(self):
        """Keep strict chat templates valid after a text-only, non-terminal turn."""
        if self.wire_api == "responses":
            self._responses_input.append({
                "role": "user",
                "content": "任务尚未通过 done 工具结束。请继续推进任务，按需调用可用工具；完成全部要求后调用 done。",
            })
            return
        self._history.append({
            "role": "user",
            "content": "任务尚未通过 done 工具结束。请继续推进任务，按需调用可用工具；完成全部要求后调用 done。",
        })

    # ---------- judge 纯文本调用 ----------
    def chat_text(self, system: str, user: str, timeout_s: float | None = None) -> str:
        """裁判用的一次性文本请求。上游故障同样走 ``_with_infra_retry``：
        裁判连不上是这台机器的责任，不能让被评的模型背（见 judge.Judge）。"""
        return self._with_infra_retry(
            lambda per_try: self._chat_text_once(system, user, per_try),
            timeout_s, "judge/chat_text")

    def _chat_text_once(self, system: str, user: str, timeout_s: float | None = None) -> str:
        if self.wire_api == "chat":
            body = {
                "model": self.model,
                "messages": ([{"role": "system", "content": system}] if system else [])
                            + [{"role": "user", "content": user}],
                "max_tokens": self.max_output_tokens,
            }
            if self.temperature is not None:
                body["temperature"] = self.temperature
            r = self._client.post(
                f"{self.base_url}/chat/completions", headers=self._headers, json=body,
                timeout=self._request_timeout(timeout_s),
            )
            _check_status(r)
            return r.json()["choices"][0]["message"].get("content") or ""
        body = self._responses_body([{"role": "user", "content": user}], instructions=system)
        response = self._stream_responses(body, timeout_s)
        return self._responses_text(response.get("output") or [])
