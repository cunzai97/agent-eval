from loopguard import LoopDetector

LV = ["ok", "warn", "block", "pause"]


def _fresh():
    return LoopDetector()


def test_repeat_escalation_aaaa():
    # A A A A：k=1,2,3,4 → ok, warn, block, pause
    d = _fresh()
    assert d.feed("bash", "cmd1") == "ok"
    assert d.feed("bash", "cmd1") == "warn"
    assert d.feed("bash", "cmd1") == "block"
    assert d.feed("bash", "cmd1") == "pause"


def test_repeat_then_fresh_resets():
    d = _fresh()
    d.feed("bash", "c")      # ok
    d.feed("bash", "c")      # warn
    assert d.feed("read", "x") == "ok"   # 全新事件，记忆清回
    assert d.feed("read", "y") == "ok"   # 全新事件，保持 ok


def test_no_downgrade_on_interruption():
    # 升过级后，被打断的非全新事件不得退级
    d = _fresh()
    d.feed("write", "f")     # ok
    d.feed("write", "f")     # warn
    d.feed("read", "g")      # 全新 → ok，记忆清回 0
    d.feed("write", "f")     # 非全新(k=1)，窗口内 write f 仅2次 → 无信号，但 last=0 → ok
    assert d.feed("write", "f") == "warn"  # 再次连续 k=2


def test_window_3of5_interrupted_ababa():
    # A B A B A：第5个 A，k=1，窗口内 A 出现3次 → warn
    d = _fresh()
    assert d.feed("t", "A") == "ok"
    assert d.feed("t", "B") == "ok"
    assert d.feed("t", "A") == "ok"   # k=1，窗口 A 仅2次
    assert d.feed("t", "B") == "ok"   # k=1，窗口 B 仅2次
    assert d.feed("t", "A") == "warn"  # k=1，窗口 A 达3次


def test_window_3of5_not_confused_with_repeat():
    # A A B A：第4个 A，k=1，窗口 A 出现3次 → warn（不是 repeat 的 block）
    d = _fresh()
    d.feed("t", "A")   # ok
    d.feed("t", "A")   # warn (k=2)
    d.feed("t", "B")   # 全新 → ok, 记忆清回
    assert d.feed("t", "A") == "warn"  # k=1 但窗口 A=3 → warn，不能升到 block


def test_normal_edit_loop_ok():
    # 读读写读读写：内容指纹各不相同，全程 ok
    d = _fresh()
    for t, h in [("read", "x"), ("read", "y"), ("write", "z"),
                 ("read", "x"), ("read", "w"), ("write", "z")]:
        assert d.feed(t, h) == "ok", (t, h)


def test_same_tool_diff_content_ok():
    # 同名工具不同内容，不构成重复
    d = _fresh()
    for h in ["1", "2", "3", "1", "2"]:
        assert d.feed("grep", h) == "ok"


def test_k3_block_before_pause():
    d = _fresh()
    d.feed("t", "Z")
    d.feed("t", "Z")   # warn
    assert d.feed("t", "Z") == "block"   # k=3
