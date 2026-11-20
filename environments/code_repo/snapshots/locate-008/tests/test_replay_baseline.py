"""复盘基线回归护栏。

REVIEW_BASELINE 的数字照抄 docs/incident-2025-11.md 的表 2（2025-11-18 结案口径）。
本用例报警 = 准入语义与结案口径不一致，处理流程见该文档第 2 节。
"""

from ratelimit.arrivals import build_requests
from ratelimit.metrics import summarize

REVIEW_BASELINE = {"total": 100, "admitted": 48, "denied_by_quota": 52, "denied_by_bucket": 0}


def test_replay_scenario_size_is_stable():
    assert len(build_requests()) == REVIEW_BASELINE["total"]


def test_replay_admission_matches_review_baseline():
    observed = summarize(build_requests())
    assert observed["admitted"] == REVIEW_BASELINE["admitted"], (
        "表2 基线 %s / 当前重放 %s" % (REVIEW_BASELINE, observed))
