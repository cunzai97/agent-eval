"""复现测试：特征只应依赖 t 及以前的价格。扰动 t 之后的价格，特征不得变化。"""
from trainsim.features import load_closes, build_features


def test_feature_invariant_to_future_prices():
    closes = load_closes("trainsim/data/prices.csv")
    t = 80
    base = build_features(closes)
    # 只扰动 t 之后的价格（严格未来），t 本身不动
    pert = list(closes)
    for i in range(t + 1, len(pert)):
        pert[i] = pert[i] * 1.5
    after = build_features(pert)
    # 截至 t（含）的特征必须完全一致
    for i in range(t + 1):
        assert base[i] == after[i], f"特征[{i}] 被未来价格污染: {base[i]} -> {after[i]}"
