from proxy import deliver


def test_long_foreground_delivered_whole():
    r = deliver("x" * 5000, "foreground", 1600)
    assert r["delivered"] == "x" * 5000
    assert r["truncated"] is False and r["reason"] is None


def test_background_with_limit_truncated():
    r = deliver("y" * 5000, "background", 1600)
    assert len(r["delivered"].encode("utf-8")) <= 1600
    assert r["truncated"] is True and r["reason"] == "hard-limit"


def test_background_no_limit_not_truncated():
    # hard_limit 缺省 → 后台也不截（默认无上限）
    r = deliver("z" * 5000, "background", None)
    assert r["delivered"] == "z" * 5000
    assert r["truncated"] is False and r["reason"] is None


def test_streaming_never_truncated():
    # 流式即使有上限也完整交付
    r = deliver("w" * 5000, "streaming", 1600)
    assert r["delivered"] == "w" * 5000
    assert r["truncated"] is False and r["reason"] is None


def test_multibyte_boundary_not_split():
    # 全是 3 字节汉字，100 字节切到第 33 字(99B)后余 1B 放不下，回退到 99B 不切断
    r = deliver("汉" * 100, "background", 100)
    assert r["truncated"] is True
    r["delivered"].encode("utf-8")  # 若切断会抛错
    assert len(r["delivered"].encode("utf-8")) == 99
    assert r["delivered"] == "汉" * 33
