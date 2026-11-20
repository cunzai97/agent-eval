"""复现：压缩前后的消息序列本应逐行一致（前缀缓存命中），但命中率掉了一半。
找到第一个失配的行，断言不存在失配 —— 它会失败，暴露失配点。"""
import json


def _load(p):
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_prefix_fully_matches():
    before = _load("prompts/before.jsonl")
    after = _load("prompts/after.jsonl")
    assert len(before) == len(after)
    for i, (b, a) in enumerate(zip(before, after), start=1):
        assert b == a, f"第 {i} 行失配:\n before={b}\n after ={a}"
