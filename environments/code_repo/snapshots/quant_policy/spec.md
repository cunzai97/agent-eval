# 混合精度量化策略 · 规格（实现 quantpol/policy.py）

`classify(graph, sensitive, quantizable, byte_cost) -> dict`
- `graph`：tensor 列表，每个 `{"name", "op", "bytes", "group"}`（group 可缺省，缺省自成一组）
- `sensitive`：敏感算子表 `{"op": "fp16"|"fp32"}`，这些算子不得量化，并给出它们的最低精度
- `quantizable`：可安全量化到 int8 的 op 名集合
- `byte_cost`：`{"fp32": 4, "fp16": 2, "int8": 1}`

## 决策（两遍）

第一遍 逐 tensor 定"意向精度"：
- op 在 sensitive → 取 sensitive[op]
- op 在 quantizable → int8
- 其余（无法识别的 op）→ fp16（保守默认，**不得降成 int8**）

第二遍 组一致性：
- 同一 group 的 tensor 最终精度必须相同，取组内**最保守**的精度（fp32 > fp16 > int8）
- 缺省 group 的 tensor 各自独立，不受组约束

## 输出

- `"tiers": {name: 最终精度}`（fp32/fp16/int8）
- `"bytes_after": int` = Σ bytes × 最终精度每元素字节数
- `"ratio": float` = bytes_after / (Σ bytes × byte_cost["fp32"])
