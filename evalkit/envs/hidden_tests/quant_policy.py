from quantpol import classify

BC = {"fp32": 4, "fp16": 2, "int8": 1}
SENS = {"layernorm": "fp16", "softmax": "fp32"}
QUANT = {"matmul", "conv"}


def test_unknown_op_stays_fp16():
    # 无法识别的 op 既不在 sensitive 也不在 quantizable → 保守默认 fp16，不得 int8
    r = classify([{"name": "x", "op": "dropout", "bytes": 80}], SENS, QUANT, BC)
    assert r["tiers"]["x"] == "fp16"
    assert r["bytes_after"] == 80 * 2


def test_quantizable_goes_int8():
    r = classify([{"name": "w", "op": "conv", "bytes": 60}], SENS, QUANT, BC)
    assert r["tiers"]["w"] == "int8"
    assert r["bytes_after"] == 60


def test_group_conflict_forces_conservative():
    # g1 组内 matmul(可量化 int8) + layernorm(fp16) → 组一致性逼 matmul 回 fp16
    graph = [
        {"name": "ln", "op": "layernorm", "bytes": 100, "group": "g1"},
        {"name": "w", "op": "matmul", "bytes": 200, "group": "g1"},
    ]
    r = classify(graph, SENS, QUANT, BC)
    assert r["tiers"]["ln"] == "fp16"
    assert r["tiers"]["w"] == "fp16"
    assert r["bytes_after"] == 100 * 2 + 200 * 2


def test_sensitive_can_be_pushed_more_conservative():
    # 同组同精度不变式对敏感张量也生效：layernorm 意向 fp16，但同组 softmax 是 fp32
    # → 整组 fp32，layernorm 被推高到 fp32
    graph = [
        {"name": "ln", "op": "layernorm", "bytes": 100, "group": "gX"},
        {"name": "sm", "op": "softmax", "bytes": 100, "group": "gX"},
    ]
    r = classify(graph, SENS, QUANT, BC)
    assert r["tiers"]["ln"] == "fp32"
    assert r["tiers"]["sm"] == "fp32"
    assert r["bytes_after"] == 100 * 4 + 100 * 4


def test_three_precision_group_collapses():
    # 组内 int8/fp16/fp32 三档并存 → 全部塌到最保守 fp32
    graph = [
        {"name": "w", "op": "matmul", "bytes": 10, "group": "g"},
        {"name": "ln", "op": "layernorm", "bytes": 20, "group": "g"},
        {"name": "sm", "op": "softmax", "bytes": 30, "group": "g"},
    ]
    r = classify(graph, SENS, QUANT, BC)
    assert r["tiers"]["w"] == "fp32"
    assert r["tiers"]["ln"] == "fp32"
    assert r["tiers"]["sm"] == "fp32"
    assert r["bytes_after"] == (10 + 20 + 30) * 4
    assert r["ratio"] == 1.0


def test_order_independence():
    # 同组张量换顺序结果必须一致（first-wins / last-wins 会挂）
    a = [
        {"name": "w", "op": "matmul", "bytes": 50, "group": "g"},
        {"name": "sm", "op": "softmax", "bytes": 50, "group": "g"},
    ]
    b = [
        {"name": "sm", "op": "softmax", "bytes": 50, "group": "g"},
        {"name": "w", "op": "matmul", "bytes": 50, "group": "g"},
    ]
    ra = classify(a, SENS, QUANT, BC)
    rb = classify(b, SENS, QUANT, BC)
    assert ra["tiers"] == rb["tiers"]
    assert ra["bytes_after"] == rb["bytes_after"]
    assert ra["tiers"]["w"] == "fp32"  # 无论顺序，组都被 softmax 拉满到 fp32


def test_multiple_groups_resolve_independently():
    # 同 op 出现在两个组：各自独立按本组最保守解析，不串组
    graph = [
        {"name": "w1", "op": "matmul", "bytes": 10, "group": "A"},
        {"name": "ln1", "op": "layernorm", "bytes": 10, "group": "A"},
        {"name": "w2", "op": "matmul", "bytes": 10, "group": "B"},
    ]
    r = classify(graph, SENS, QUANT, BC)
    assert r["tiers"]["w1"] == "fp16"  # A 组有敏感 → fp16
    assert r["tiers"]["w2"] == "int8"  # B 组只有可量化 → int8


def test_mixed_group_with_unknown():
    # 组内：未知(fp16) + 可量化(int8) + 敏感(fp32) → 整组 fp32
    graph = [
        {"name": "d", "op": "dropout", "bytes": 5, "group": "g"},
        {"name": "w", "op": "conv", "bytes": 15, "group": "g"},
        {"name": "sm", "op": "softmax", "bytes": 25, "group": "g"},
    ]
    r = classify(graph, SENS, QUANT, BC)
    for n in ("d", "w", "sm"):
        assert r["tiers"][n] == "fp32"
    assert r["bytes_after"] == (5 + 15 + 25) * 4
