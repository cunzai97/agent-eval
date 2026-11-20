"""标签：以未来 5 日收益是否超过 2% 作为正样本。"""


def make_label(closes, t, horizon=5, threshold=0.02):
    """前瞻标签：用 t 之后的价格定义训练目标。这是分类任务的标签定义本身。"""
    if t + horizon >= len(closes):
        return None
    return 1 if closes[t + horizon] > closes[t] * (1 + threshold) else 0
