"""全局令牌桶：与租户配额无关，只压物理容量。

回填为连续回填（按经过的时间 / REFILL_INTERVAL_S 计），不做整秒截断。
"""

from ratelimit import config


class TokenBucket:
    def __init__(self, capacity=config.BUCKET_CAPACITY, refill_interval_s=config.REFILL_INTERVAL_S):
        self.capacity = float(capacity)
        self.refill_interval_s = float(refill_interval_s)
        self.tokens = float(capacity)
        self.last_refill = 0.0

    def _refill(self, now):
        gained = (now - self.last_refill) / self.refill_interval_s
        if gained > 0:
            self.tokens = min(self.capacity, self.tokens + gained)
        self.last_refill = now

    def available(self, now):
        self._refill(now)
        return self.tokens

    def try_take(self, now, cost):
        """扣 cost 个令牌；余额不足则整笔失败（不做部分放行）。"""
        self._refill(now)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False
