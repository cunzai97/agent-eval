"""压缩核心。保留词表前 int(len(LEXICON) * threshold) 个词。"""
from .lexicon import LEXICON


def _keep_count(threshold):
    # threshold 越高保留越少：保留前 (1 - threshold) 比例
    return int(len(LEXICON) * (1.0 - threshold))


def compress_document(text, threshold):
    keep = set(LEXICON[:_keep_count(threshold)])
    out = [w for w in text.lower().split() if w in keep]
    return out
