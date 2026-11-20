"""epoch 配额分配语义：#4180 加权 + #4412 基础配额取整口径。"""

from ratelimit import config
from ratelimit.allocator import epoch_demands, fair_share, weights_of


def test_favored_tenant_counts_double_weight():
    assert weights_of({"web": 3, "batch": 3}) == {"web": 3, "batch": 6}


def test_small_tenant_is_not_starved_by_rounding():
    # #4412：ceil 让 0.19 / 0.37 这类权重先拿到 1 个基础配额，再受需求封顶
    assert fair_share({"web": 1, "batch": 1, "mobile": 40}, 8) == {
        "web": 1, "batch": 1, "mobile": 8}


def test_base_quota_sum_can_exceed_capacity():
    # #4412 的副作用：需求封顶只压单个租户，不回收余量，所以之和可以 > CAP_PER_EPOCH
    assert fair_share({"web": 5, "batch": 3, "mobile": 4}, 8) == {
        "web": 3, "batch": 3, "mobile": 3}


def test_quota_is_never_above_own_demand():
    quota = fair_share({"web": 2, "batch": 30, "mobile": 30}, config.CAP_PER_EPOCH)
    assert all(quota[t] <= d for t, d in (("web", 2), ("batch", 30), ("mobile", 30)))


def test_no_capacity_or_no_demand_gives_zero_everywhere():
    assert fair_share({"web": 3, "batch": 1}, 0) == {"web": 0, "batch": 0}
    assert fair_share({}, config.CAP_PER_EPOCH) == {}


def test_epoch_demands_counts_requests_per_tenant():
    rows = [{"tenant": "web"}, {"tenant": "batch"}, {"tenant": "web"}]
    assert epoch_demands(rows) == {"web": 2, "batch": 1}
