from loopguard import LoopDetector


def test_fresh_events_ok():
    d = LoopDetector()
    for t, h in [("read", "a"), ("write", "b"), ("read", "c")]:
        assert d.feed(t, h) == "ok"


def test_normal_edit_loop_ok():
    # read x / read y / write z / read x：x 被打断，窗口内 2 次，不得升级
    d = LoopDetector()
    assert d.feed("read", "x") == "ok"
    assert d.feed("read", "y") == "ok"
    assert d.feed("write", "z") == "ok"
    assert d.feed("read", "x") == "ok"
