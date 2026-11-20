from proxy import deliver


def test_foreground_never_truncates():
    # 前台长回复应完整送达，当前会被静默截断
    r = deliver("hello world", "foreground", 3)
    assert r["delivered"] == "hello world"
    assert r["truncated"] is False
    assert r["reason"] is None
