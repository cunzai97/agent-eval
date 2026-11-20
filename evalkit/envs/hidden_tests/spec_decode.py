from specdec import simulate


def test_dual_buffer_faster_than_serial():
    r = simulate(draft_tps=100, target_tps=10, k=4, rounds=5,
                 accepted_trace=[3, 1, 4, 0, 2])
    d, v = 4 / 100, 5 / 10
    assert r["wall_dual"] == d + 4 * max(d, v) + v
    assert r["wall_serial"] == 5 * (d + v)
    assert r["wall_dual"] < r["wall_serial"]
    assert r["speedup"] == r["wall_serial"] / r["wall_dual"]


def test_generated_total_counts_bonus():
    r = simulate(draft_tps=50, target_tps=5, k=2, rounds=3,
                 accepted_trace=[0, 2, 1])
    # 每轮接受 a + 1 bonus
    assert r["generated_total"] == (0 + 1) + (2 + 1) + (1 + 1)


def test_all_reject_still_generates_bonus():
    r = simulate(draft_tps=100, target_tps=10, k=4, rounds=4,
                 accepted_trace=[0, 0, 0, 0])
    assert r["generated_total"] == 4  # 每轮只有 1 个 bonus
    assert r["wall_dual"] > 0


def test_speedup_monotonic_in_draft_speed():
    # 草稿越快，双缓冲收益越小（瓶颈转向校验），speedup 单调不增
    sp = [simulate(draft_tps=s, target_tps=10, k=4, rounds=8,
                   accepted_trace=[2] * 8)["speedup"] for s in (50, 100, 500, 1000)]
    assert sp[0] >= sp[1] >= sp[2] >= sp[3]
