"""特征构建。基于 prices.csv 的 close 序列构造回测特征。"""
import csv


def load_closes(path):
    closes = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            closes.append(float(row["close"]))
    return closes


def window(values, t, k):
    """返回以 t 为中心、长度为 k 的窗口切片。"""
    # 注意：k 为奇数时中心对称
    lo = t - k // 2
    hi = t + (k + 1) // 2
    return values[lo:hi]


def build_features(closes):
    """为每个交易日 t 生成 (ret_5, vol_5) 特征。"""
    feats = []
    for t in range(len(closes)):
        w = window(closes, t, 5)
        if len(w) < 5:
            feats.append((0.0, 0.0))
            continue
        ret = closes[t] / closes[t - 1] - 1.0 if t > 0 else 0.0
        vol = _stdev(w)
        feats.append((ret, vol))
    return feats


def _stdev(xs):
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5
