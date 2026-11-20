from specdec import simulate


def test_single_round_no_pipeline_gain():
    # 只有一轮时没有可重叠的下一轮草稿，双缓冲退化为串行
    r = simulate(draft_tps=100, target_tps=10, k=4, rounds=1, accepted_trace=[2])
    assert r["wall_dual"] == 0.04 + 0.5
    assert r["wall_serial"] == 0.04 + 0.5
    assert r["speedup"] == 1.0
    assert r["generated_total"] == 3
