from data.universe import members_asof
from features.lag import lagged
from features.store import feature_asof
from split.walk import purged_train_indices
from train.scale import normalize_fold


def test_feature_revision_published_in_future_is_not_visible():
    rows = [
        {"code": "A", "observation_date": "2024-01-01T00:00:00Z", "published_at": "2024-01-02T00:00:00Z", "value": 1},
        {"code": "A", "observation_date": "2024-01-01T00:00:00Z", "published_at": "2024-02-01T00:00:00Z", "value": 99},
    ]
    assert feature_asof(rows, "A", "2024-01-10T00:00:00Z")["value"] == 1


def test_historical_universe_includes_then_member_even_if_absent_today():
    history = [
        {"code": "OLD", "effective_from": "2020-01-01", "effective_to": "2024-06-01"},
        {"code": "NEW", "effective_from": "2024-06-01", "effective_to": None},
    ]
    assert members_asof(["NEW"], history, "2024-01-15") == ["OLD"]


def test_scaler_is_fit_on_train_only():
    result = normalize_fold([1.0, 3.0], [1000.0], [2000.0])
    assert result["center"] == 2.0
    assert result["train"] == [-1.0, 1.0]


def test_walk_forward_purge_already_excludes_overlapping_labels():
    assert purged_train_indices([3, 5, 8, 11], validation_start=9) == [0, 1, 2]


def test_feature_lag_is_causal():
    assert lagged([10, 20, 30]) == [None, 10, 20]
