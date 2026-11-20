from quantpol import classify

BC = {"fp32": 4, "fp16": 2, "int8": 1}


def test_sensitive_keeps_rule_precision():
    # 单个敏感算子、无 group：保留规则精度
    r = classify([{"name": "ln", "op": "layernorm", "bytes": 100}],
                 {"layernorm": "fp16"}, {"matmul"}, BC)
    assert r["tiers"]["ln"] == "fp16"
    assert r["bytes_after"] == 100 * 2
