# 上游推理引擎变更历史

## v2.7.1
- 修复：分页 KV cache 在最后一块不满 block 时**未向上取整页数**，导致显存占用被低估、
  长序列静默超卡而崩溃。改为 `ceil(length / block)` 后崩溃消失。

## v2.6.4
- 优化：把注意力 logits 的中间张量从 fp32 降为 fp16，降低峰值显存。（干扰项：与崩溃无关）

## v2.5.9
- 修复：多卡张量并行时 all-reduce 的 buffer 未随 world_size 缩放，导致 OOM。（干扰项：另一类 OOM）

## v2.4.2
- 新增：支持 4-bit KV cache 量化，`quant_bits` 参数生效。
