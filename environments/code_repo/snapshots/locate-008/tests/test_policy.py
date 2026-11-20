"""准入决策：epoch 配额与全局令牌桶两道闸门（#3980 epoch 口径）。"""

from ratelimit import config
from ratelimit.policy import admit, cost_of, epoch_of


def test_cost_is_weight_plus_one():
    assert [cost_of({"weight": w}) for w in (0, 1, 2)] == [1, 2, 3]


def test_epoch_index_uses_floor_division_at_boundary():
    assert epoch_of({"at": config.EPOCH_S - 1}) == 0
    assert epoch_of({"at": config.EPOCH_S}) == 1


def test_same_second_requests_keep_arrival_order():
    rows = [
        {"id": "a", "at": 5, "tenant": "web", "weight": 0},
        {"id": "b", "at": 5, "tenant": "web", "weight": 0},
    ]
    assert [req["id"] for req, _ok, _reason in admit(rows)] == ["a", "b"]


def test_quota_gate_denies_beyond_cap_per_epoch():
    rows = [{"id": str(i), "at": 1 + i, "tenant": "web", "weight": 0} for i in range(10)]
    reasons = [reason for _req, _ok, reason in admit(rows)]
    assert reasons.count("ok") == 8
    assert reasons.count("quota") == 2


def test_bucket_gate_can_deny_even_when_quota_is_enough():
    # 6 条 cost=3 的 web 请求：配额给到 6 条，令牌桶只放得下 5 条
    rows = [{"id": str(i), "at": 1 + i, "tenant": "web", "weight": 2} for i in range(6)]
    reasons = [reason for _req, _ok, reason in admit(rows)]
    assert reasons.count("ok") == 5
    assert reasons.count("bucket") == 1
    assert reasons.count("quota") == 0
