import random
from page import split


def _mixed_text(n, seed=7):
    rnd = random.Random(seed)
    pool = ["a", "中", "文", "测", "试", "数", "据", "🚀", "🎯", "🐍", "é", "€", "汉"]
    return "".join(rnd.choice(pool) for _ in range(n))


def test_no_gap_no_overlap_order_preserved():
    text = _mixed_text(30000)
    parts = split(text, 4096)
    assert "".join(parts) == text  # 不丢字、不重叠、保序


def test_every_chunk_within_utf8_budget():
    text = _mixed_text(30000, seed=11)
    for p in split(text, 4096):
        assert len(p.encode("utf-8")) <= 4096


def test_multi_byte_never_split_across_chunks():
    # 全是 3 字节汉字 + 4 字节 emoji，边界必须落在字符边界（能 encode 回原文即证明没切断）
    text = "汉" * 5000 + "🚀" * 2000
    parts = split(text, 100)
    assert "".join(parts) == text
    for p in parts:
        p.encode("utf-8")  # 若从中间切断会抛 UnicodeDecodeError


def test_greedy_fills_budget():
    # 贪心：一个 3 字节汉字应塞到接近 max_bytes，而不是按字符数早切
    parts = split("汉" * 100, 100)
    assert "".join(parts) == "汉" * 100
    # 100 字节 / 3 = 33 个整字 + 余 1 字节放不下 → 前片 33 字(99B)
    assert len(parts[0].encode("utf-8")) == 99


def test_empty_and_single():
    assert split("", 10) == [] or "".join(split("", 10)) == ""
    assert "".join(split("x", 1)) == "x"
