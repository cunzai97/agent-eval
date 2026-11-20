from data.universe import members_asof
from features.store import feature_asof
from train.scale import normalize_fold


def build_fold(current_codes, membership_rows, feature_rows, train, validation, test, as_of):
    members = members_asof(current_codes, membership_rows, as_of)
    features = {code: feature_asof(feature_rows, code, as_of) for code in members}
    scaled = normalize_fold(train, validation, test)
    return {"members": members, "features": features, "scaled": scaled}
