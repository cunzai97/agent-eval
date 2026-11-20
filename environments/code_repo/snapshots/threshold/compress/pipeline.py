"""参数流：解析 CLI → 压缩。"""
from .compress import compress_document


def build_cli(args):
    threshold = 0.5  # 默认
    i = 0
    while i < len(args):
        if args[i] == "--threshold":
            threshold = float(args[i + 1])
            i += 1
        i += 1
    return {"threshold": threshold}


def run_pipeline(args):
    cfg = build_cli(args)
    # bug: 这里没有把 cfg["threshold"] 传下去，直接用了默认值
    text = "the quick brown fox jumps over the lazy dog of the and to in a that with from this had there"
    return compress_document(text, 0.5)
