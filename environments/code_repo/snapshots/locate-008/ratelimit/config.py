"""网关准入的静态配置：运行期只读这里的常量。

docs/ 下的复盘文档是历史归档，不随代码更新；常量与准入语义以本文件与
CHANGELOG.md 为准。
"""

BUCKET_CAPACITY = 14        # 令牌桶容量：2025-06 (#3620) 设定后未再变更
REFILL_INTERVAL_S = 4       # 每回填 1 个令牌的间隔（秒）：2025-06 (#3620) 设定后未再变更
EPOCH_S = 60                # 配额周期长度（秒）
CAP_PER_EPOCH = 8           # 单个 epoch 可分配给各租户的配额上限：2025-06 (#3620) 设定后未再变更
REQUEST_TOTAL = 100         # 复盘场景固定重放的请求数
SCENARIO_SEED = 20251107    # 复盘场景的请求流种子（取事故日，保证结果可复现）
TENANTS = ("web", "batch", "mobile")
FAVORED_TENANT = "batch"    # 参与配额加权时按 2 倍权重计入的租户，见 #4180
