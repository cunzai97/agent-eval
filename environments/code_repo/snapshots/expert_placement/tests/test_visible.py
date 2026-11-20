from moesim import place_and_replay


def test_single_expert_stays_warm_in_ram():
    experts = {"a": 100}
    caps = {"vram_bytes": 300, "ram_bytes": 200}
    cost = {"vram": 1.0, "ram": 8.0, "disk": 40.0}
    res = place_and_replay(experts, caps, cost, [[ "a" ]])
    # 首次访问 rank=1 → warm → 落 ram，计一次 ram 读取
    assert res["final_tiers"]["a"] == "ram"
    assert res["total_cost"] == 8.0
    assert res["evictions"] == []
