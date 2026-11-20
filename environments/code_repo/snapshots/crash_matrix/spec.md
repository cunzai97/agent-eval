# VRAM 崩溃预测器 · 规格（实现 crashsim/predictor.py）

`predict(length, block, quant_bits, card_vram_bytes, base_bytes, threshold) -> dict`
返回 `{"vram_bytes": int, "crash": bool}`。

- `length`：序列 token 数（int ≥ 0）。`block`：分页 block 大小（int ≥ 1）。
- `quant_bits`：KV cache 每 token 每层的位数（int，如 8/4/2）。
- `card_vram_bytes`：卡显存容量。`base_bytes`：模型权重常驻显存（与 length 无关）。
- `threshold`：崩溃阈值系数（0<θ≤1）。

## 计算

- KV cache 页数 = `ceil(length / block)`。
- `vram_bytes = base_bytes + 页数 × block × quant_bits`。
- `crash = (vram_bytes / card_vram_bytes) >= threshold`（达到或超过阈值即崩溃）。

注意：页数向上取整，最后一块不满 block 也按整块占显存。
