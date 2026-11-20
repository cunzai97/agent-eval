"""分页气泡切分。实现 split，契约见 spec.md。"""


def split(text, max_bytes):
    """按 UTF-8 字节预算贪心切分，边界落在字符边界，拼接还原原文。"""
    # TODO: 逐字符累加 UTF-8 字节数，达到 max_bytes 即切，不切断多字节字符
    raise NotImplementedError
