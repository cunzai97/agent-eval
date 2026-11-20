"""Hidden checks for snapshot debug-007（异步 MTP 生命周期）。

可见套件（tests/test_lifecycle.py，9 条）钉的是**终态**：某个调用序列跑完之后
`worker.states[seq]` 里各个字段等于什么。它对下面两件事完全瞎：

1. **server 发给 worker 的调用协议**。可见测试从不见调用序列，所以
   "每个 chunk 都发一次 prompt_begin"、"prompt_begin 晚于第一次 enqueue"、
   "prompt_finish 之后又补一次 begin"、"把 chunks 摊平成一次 enqueue" 都能把
   终态凑对；而 `if restored_position == 0` 这类边界推断只是换了个地方活着。
2. **一个 slot 的操作对其它 slot 的影响面**。可见测试里的多 slot 用例（test 7）
   每个 slot 都是"begin→enqueue→finish 一口气走完"，所以在 prompt_begin /
   prompt_finish 里顺手清掉**所有** sequence 的队列（"把旧异步状态清干净"的过度
   泛化）在可见套件下不留下任何痕迹——那正是题面禁止的串 slot 污染。

另外两类只有构造新序列才看得见：半途而来的新边界既不重复也不丢落地记账
（可见 test 3 只看了 prompt_begin 那一刻，没看之后的 prompt_finish）；
`enqueue_prompt` 的模式守卫被删掉（"这个 RuntimeError 就是症状"）后，
generation-local 期间照样能塞 prompt 批次，而可见套件从不这样调用它。

定标口径（实测，不是手推）：把 36 份历史尝试的 edit_file 调用重放到干净快照，其中可见
全绿的 32 份（30 份历史判对 + 2 份流程闸门放宽后新准入）在本模块 17 条上全部 17/17，
零误杀；未修快照 0/17。另构造八份单点变异复验，六份"可见全绿 + 本模块红"、各被不同
用例抓住（跨 slot 清队列 → 第 2 组；每 chunk 一次边界 → 第 1 组；小于 50 的非零位置仍
被当冷启动 → 第 3 组；被接管的旧队列重复记账 → 第 5 组；删掉 enqueue_prompt 的模式守卫
→ 第 6 组；只写死 0/1 两个 slot → 第 1、2、7 组——实测 13/17，被
test_one_prompt_is_exactly_one_boundary_before_all_chunks（Spy(3) 打到 slot 2）、
第 2 组两条（SpeculativeWorker(4)）与第 7 组各抓一次），另外两份（放宽 verify_generation 的
行号守卫、cancel 遍历全部 slot）在可见通道就已经红。
有一条刻意**不**钉：cancel 之后 `drained_positions` 该怎么算——30 份历史判对里有 2 份
把被取消的作业 extend 进 drained，题面没说、可见套件也没考，钉上去就是罚正确答案。
字段名与模式字符串沿用可见套件已经钉死的拼写；状态一律重新从 `worker.states[seq]` 读
（cancel 用新 SequenceState 替换旧对象的写法同样合法，所以不依赖对象身份）。
"""
import inspect

import pytest

from server.scheduler import Scheduler
from speculative.worker import SpeculativeWorker

BOUNDARY = ("prompt_begin", "enqueue_prompt", "prompt_finish")


def _snapshot(state):
    """只读可见套件已经钉住的字段，避免依赖 SequenceState 的对象身份。"""
    return {
        "mode": state.mode,
        "prompt_start": state.prompt_start,
        "queued_positions": list(state.queued_positions),
        "drained_positions": list(state.drained_positions),
        "verify_rows": state.verify_rows,
        "failed": state.failed,
    }


class Spy(SpeculativeWorker):
    """记录 server 打到 worker 上的生命周期调用（按被调方法自己的形参顺序取实参）。"""

    def __init__(self, n_sequences=2):
        super().__init__(n_sequences)
        self.calls = []

    def _record(self, name, args, kwargs):
        try:
            bound = inspect.signature(getattr(SpeculativeWorker, name)).bind(self, *args, **kwargs)
            values = list(bound.args[1:])          # 去掉 self
        except TypeError:                          # 签名被改过：退化成原样记录
            values = list(args) + list(kwargs.values())
        self.calls.append((name, tuple(values)))

    def prompt_begin(self, *args, **kwargs):
        self._record("prompt_begin", args, kwargs)
        return SpeculativeWorker.prompt_begin(self, *args, **kwargs)

    def enqueue_prompt(self, *args, **kwargs):
        self._record("enqueue_prompt", args, kwargs)
        return SpeculativeWorker.enqueue_prompt(self, *args, **kwargs)

    def prompt_finish(self, *args, **kwargs):
        self._record("prompt_finish", args, kwargs)
        return SpeculativeWorker.prompt_finish(self, *args, **kwargs)

    def verify_generation(self, *args, **kwargs):
        self._record("verify_generation", args, kwargs)
        return SpeculativeWorker.verify_generation(self, *args, **kwargs)

    def cancel(self, *args, **kwargs):
        self._record("cancel", args, kwargs)
        return SpeculativeWorker.cancel(self, *args, **kwargs)


def _lifecycle_trace(spy, names=BOUNDARY):
    """调用序列规整成 [(名字, 实参元组)]；列表/range 统一成 tuple。"""
    return [(name, tuple(tuple(v) if isinstance(v, (list, tuple, range)) else v for v in values))
            for name, values in spy.calls if name in names]


# ---------------------------------------------------------------------------
# 1. server 侧的调用协议：一个 prompt 一次边界，边界在最前，finish 在最后
# ---------------------------------------------------------------------------
def test_one_prompt_is_exactly_one_boundary_before_all_chunks():
    worker = Spy(3)
    server = Scheduler(worker)
    server.start_prompt(2, 12345, [[12345, 12346], [12347]])
    assert _lifecycle_trace(worker) == [
        ("prompt_begin", (2, 12345)),
        ("enqueue_prompt", (2, (12345, 12346))),
        ("enqueue_prompt", (2, (12347,))),
        ("prompt_finish", (2,)),
    ], "server 必须为每个新 prompt 发且只发一次 prompt_begin（在任何 enqueue 之前），并在全部 chunk 之后 finish"


def test_boundary_is_emitted_for_a_cold_prompt_too_and_no_extra_rounds():
    worker = Spy(2)
    server = Scheduler(worker)
    server.start_prompt(0, 0, [[0]])
    assert _lifecycle_trace(worker) == [
        ("prompt_begin", (0, 0)),
        ("enqueue_prompt", (0, (0,))),
        ("prompt_finish", (0,)),
    ]
    server.start_prompt(0, 1, [[1]])
    trace = _lifecycle_trace(worker)
    assert [name for name, _ in trace].count("prompt_begin") == 2, "两次请求 = 两次边界，不多不少"
    assert [name for name, _ in trace].count("prompt_finish") == 2
    assert trace[-3:] == [("prompt_begin", (0, 1)), ("enqueue_prompt", (0, (1,))), ("prompt_finish", (0,))]


# ---------------------------------------------------------------------------
# 2. 边界与收尾的作用域：不得波及别的 sequence（可见 test 7 看不到这一面）
# ---------------------------------------------------------------------------
def test_new_prompt_on_one_slot_leaves_another_slots_in_flight_prompt_intact():
    worker = SpeculativeWorker(4)
    server = Scheduler(worker)
    worker.prompt_begin(0, 500)
    worker.enqueue_prompt(0, [500, 501])          # slot 0 的 prompt 还挂在队列里
    worker.prompt_begin(2, 900)
    worker.enqueue_prompt(2, [900])
    before = {0: _snapshot(worker.states[0]), 2: _snapshot(worker.states[2])}

    server.start_prompt(1, 77, [[77, 78]])        # 另一个 slot 的新请求（含 begin 与 finish）

    for seq in (0, 2):
        assert _snapshot(worker.states[seq]) == before[seq], \
            f"slot {seq} 在途的 prompt 队列/模式被别的 slot 的请求改掉了：这就是串 slot 污染"
    assert worker.states[0].queued_positions == [500, 501]


def test_cancel_touches_only_the_target_sequence_with_a_pending_queue():
    worker = SpeculativeWorker(4)
    worker.prompt_begin(0, 0)
    worker.enqueue_prompt(0, [0, 1])
    worker.prompt_begin(1, 4096)
    worker.enqueue_prompt(1, [4096, 4097])       # 未 finish，正在途
    worker.prompt_begin(2, 65536)
    worker.enqueue_prompt(2, [65536])
    worker.prompt_finish(2)
    worker.verify_generation(2, [0])
    worker.mark_failed(2)
    pending_before = _snapshot(worker.states[0])

    worker.cancel(1)

    assert _snapshot(worker.states[0]) == pending_before, "cancel(1) 不得动 slot 0 的在途队列"
    assert worker.states[2].verify_rows == 1 and worker.states[2].failed is True, \
        "cancel(1) 不得清掉别的 sequence 的进度与失败标记"
    cancelled = _snapshot(worker.states[1])
    assert cancelled["mode"] == "idle"
    assert cancelled["queued_positions"] == []
    assert cancelled["failed"] is False


# ---------------------------------------------------------------------------
# 3. "非零恢复" 是任意非零：不得用阈值/魔数猜请求边界
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("pos", [1, 2, 17, 512, 4096, 65536, 1048576])
def test_any_non_zero_restore_reenters_absolute_mode(pos):
    worker = SpeculativeWorker(2)
    server = Scheduler(worker)
    server.start_prompt(0, pos, [[pos, pos + 1]])
    state = worker.states[0]
    assert state.prompt_start == pos, "prompt_start 必须是这次恢复的绝对位置，不能是猜出来的 0"
    assert state.drained_positions[-2:] == [pos, pos + 1]
    assert state.mode == "generation_local"
    server.generate(0, 3)
    assert worker.states[0].verify_rows == 3


# ---------------------------------------------------------------------------
# 4. 连续请求的记账：计数器按请求重置，位置按请求累积，顺序不丢
# ---------------------------------------------------------------------------
def test_long_session_on_one_slot_keeps_accounts_per_request():
    worker = SpeculativeWorker(2)
    server = Scheduler(worker)
    idle_slot = _snapshot(worker.states[1])
    for index, pos in enumerate((0, 300, 1200, 9000, 70000)):
        server.start_prompt(0, pos, [[pos], [pos + 1, pos + 2]])
        server.generate(0, index + 1)
        state = _snapshot(worker.states[0])
        assert state["verify_rows"] == index + 1, "verify_rows 跨请求累计了：上一请求的行数还在账上"
        assert state["queued_positions"] == []
        assert state["drained_positions"][-3:] == [pos, pos + 1, pos + 2]
        assert state["failed"] is False
        assert state["mode"] == "generation_local"
    assert _snapshot(worker.states[1]) == idle_slot, "另一个 slot 必须从头到尾没被碰过"


# ---------------------------------------------------------------------------
# 5. 半途而来的新边界：既不重复记账也不丢账（可见 test 3 只看了那一刻的队列）
# ---------------------------------------------------------------------------
def test_mid_flight_new_request_accounts_each_position_exactly_once():
    worker = SpeculativeWorker(2)
    worker.prompt_begin(0, 0)
    worker.enqueue_prompt(0, [0, 1])
    worker.mark_failed(0)
    worker.prompt_begin(0, 100)                   # 上一个 prompt 没 finish，新请求就来了
    state = worker.states[0]
    assert state.queued_positions == []
    assert state.failed is False
    assert state.mode == "prompt_absolute"
    worker.enqueue_prompt(0, [100, 101])
    worker.prompt_finish(0)
    drained = list(worker.states[0].drained_positions)
    assert sorted(drained) == [0, 1, 100, 101], "被接管的旧队列要么丢了要么被记了两次"
    assert drained[-4:] == [0, 1, 100, 101], "旧队列必须先落到已处理账上，再按顺序接上这次的 chunk"


def test_reused_slot_after_cancel_is_a_fresh_non_zero_request():
    worker = SpeculativeWorker(2)
    server = Scheduler(worker)
    worker.prompt_begin(0, 0)
    worker.enqueue_prompt(0, [0, 1])
    worker.prompt_finish(0)
    worker.verify_generation(0, [0])
    worker.prompt_begin(0, 10)
    worker.enqueue_prompt(0, [10, 11])            # 在途被取消
    worker.mark_failed(0)
    worker.cancel(0)
    state = worker.states[0]
    assert state.mode == "idle"
    assert state.queued_positions == []
    assert state.failed is False

    server.start_prompt(0, 10, [[10, 11]])        # 同一 slot 复用，非零恢复
    state = worker.states[0]
    assert state.prompt_start == 10
    assert state.drained_positions[-2:] == [10, 11]
    assert state.mode == "generation_local"
    server.generate(0, 2)
    assert worker.states[0].verify_rows == 2


# ---------------------------------------------------------------------------
# 6. 模式守卫本身是生命周期的一部分：删守卫 = 症状修
# ---------------------------------------------------------------------------
def test_prompt_batches_are_still_rejected_after_the_prompt_finished():
    worker = SpeculativeWorker(2)
    worker.prompt_begin(0, 300)
    worker.enqueue_prompt(0, [300])
    worker.prompt_finish(0)
    with pytest.raises(RuntimeError):
        worker.enqueue_prompt(0, [301])           # 已经 generation-local，不能再塞 prompt 批次
    assert worker.states[0].queued_positions == []


def test_generation_rows_must_stay_local_contiguous_after_a_restore():
    worker = SpeculativeWorker(2)
    server = Scheduler(worker)
    server.start_prompt(0, 63000, [[63000, 63001]])
    for rows in ([1], [0, 2], [63002], [0, 1, 5]):
        with pytest.raises(RuntimeError):
            worker.verify_generation(0, rows)
    assert worker.states[0].verify_rows == 0
    server.generate(0, 2)
    assert worker.states[0].verify_rows == 2
    server.start_prompt(0, 64000, [[64000]])
    server.generate(0, 1)
    assert worker.states[0].verify_rows == 1


# ---------------------------------------------------------------------------
# 7. slot 数量与索引：不得把 0/1 写死
# ---------------------------------------------------------------------------
def test_four_slots_run_independent_requests_with_their_own_positions():
    worker = SpeculativeWorker(4)
    server = Scheduler(worker)
    starts = {0: 0, 1: 1024, 2: 8192, 3: 400000}
    for seq, pos in starts.items():
        server.start_prompt(seq, pos, [[pos], [pos + 1]])
        server.generate(seq, seq + 1)
    for seq, pos in starts.items():
        state = worker.states[seq]
        assert state.prompt_start == pos
        assert state.drained_positions == [pos, pos + 1]
        assert state.verify_rows == seq + 1
        assert state.mode == "generation_local"
        assert state.failed is False
    assert len(worker.states) >= 4
