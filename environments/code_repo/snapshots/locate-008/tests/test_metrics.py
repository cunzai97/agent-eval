"""可观测性输出（#4455 只动标签，不动准入）。"""

from ratelimit.metrics import render, summarize


def test_render_covers_every_request_once():
    rows = [
        {"id": "a", "at": 1, "tenant": "web", "weight": 0},
        {"id": "b", "at": 2, "tenant": "batch", "weight": 2},
    ]
    out = render(rows)
    assert len(out) == 2
    assert {row["scope"] for row in out} == {"web", "batch"}


def test_summarize_partitions_every_request():
    rows = [{"id": str(i), "at": 1 + i, "tenant": "web", "weight": 0} for i in range(12)]
    stats = summarize(rows)
    assert stats["total"] == 12
    assert stats["admitted"] + stats["denied_by_quota"] + stats["denied_by_bucket"] == 12
