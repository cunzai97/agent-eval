# 压缩阈值参数流 · 规格（修复 compress/ 下 threshold 的静默失效）

`compress_document(text, threshold)` 按 `threshold`（0..1）决定压缩强度：
阈值越高，保留的"重要词"越少、输出越短。`build_cli(args)` 从命令行解析 `--threshold`，
`run_pipeline(args)` 把解析结果传给 `compress_document`。

bug：threshold 在参数流中途被一个硬编码的默认值覆盖，导致无论命令行传多少，
实际压缩强度都一样。请追踪参数流，找到被覆盖的那一层，让 threshold 真正生效。

验收：`--threshold 0.2 / 0.5 / 0.8` 三种入参，输出的保留词数必须**严格递减**
（0.2 保留最多，0.8 保留最少），且三者的输出不能两两相同。
