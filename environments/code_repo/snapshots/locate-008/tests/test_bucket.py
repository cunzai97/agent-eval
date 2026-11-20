"""全局令牌桶语义（#3620）：连续回填、容量封顶、整笔扣减。"""

from ratelimit.bucket import TokenBucket


def test_refill_is_continuous_not_whole_second():
    bucket = TokenBucket(capacity=2, refill_interval_s=10)
    assert bucket.try_take(0, 2) is True
    assert bucket.available(3) == 0.3          # 经过 3 秒 / 间隔 10 秒，不是 0


def test_refill_never_exceeds_capacity():
    bucket = TokenBucket(capacity=5, refill_interval_s=4)
    assert bucket.available(10_000) == 5


def test_take_is_all_or_nothing():
    bucket = TokenBucket(capacity=14, refill_interval_s=4)
    assert bucket.try_take(0, 14) is True
    assert bucket.try_take(0, 1) is False      # 余额不足：整笔失败
    assert bucket.available(0) == 0            # 失败不扣令牌
