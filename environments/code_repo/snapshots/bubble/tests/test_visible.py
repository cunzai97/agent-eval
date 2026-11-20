from page import split


def test_ascii_roundtrip():
    parts = split("hello world", 7)
    assert "".join(parts) == "hello world"
    assert all(len(p.encode("utf-8")) <= 7 for p in parts)
