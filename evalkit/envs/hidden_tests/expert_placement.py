from moesim import place_and_replay

EXPERTS = {n: 100 for n in "abcd"}
CAPS = {"vram_bytes": 300, "ram_bytes": 200}
COST = {"vram": 1.0, "ram": 8.0, "disk": 40.0}
TRACE = [
    ["a"], ["b"], ["c"], ["a"], ["a"], ["b"], ["b"], ["c"],
    ["a", "b", "c"], ["d"], ["d"], ["d"], ["a"], ["b"],
]


def test_full_replay_total_cost():
    res = place_and_replay(EXPERTS, CAPS, COST, TRACE)
    assert res["total_cost"] == 72.0


def test_full_replay_eviction_sequence():
    res = place_and_replay(EXPERTS, CAPS, COST, TRACE)
    assert res["evictions"] == [
        (2, "a", "ram", "disk"),
        (3, "b", "ram", "disk"),
        (11, "a", "vram", "ram"),
        (12, "b", "vram", "ram"),
        (13, "c", "vram", "ram"),
    ]


def test_full_replay_final_tiers():
    res = place_and_replay(EXPERTS, CAPS, COST, TRACE)
    assert res["final_tiers"] == {"a": "vram", "b": "vram", "c": "ram", "d": "vram"}


def test_hot_threshold_requires_three_distinct_steps():
    # 同一专家被访问 2 个不同步仍只是 warm，落 ram；第 3 个不同步才升 vram
    res = place_and_replay({"a": 100}, {"vram_bytes": 300, "ram_bytes": 200}, COST, [["a"], ["a"]])
    assert res["final_tiers"]["a"] == "ram"
    assert res["total_cost"] == 16.0
    res3 = place_and_replay({"a": 100}, {"vram_bytes": 300, "ram_bytes": 200}, COST, [["a"], ["a"], ["a"]])
    assert res3["final_tiers"]["a"] == "vram"
    assert res3["total_cost"] == 8.0 + 8.0 + 1.0


def test_lru_tie_breaks_by_name():
    # ram 满、后一位进来时，驱逐 la 最小的（最早访问的）
    ex = {n: 100 for n in "xyz"}
    res = place_and_replay(ex, CAPS, COST, [["x"], ["y"], ["z"]])
    assert res["evictions"] == [(2, "x", "ram", "disk")]
    assert res["final_tiers"]["z"] == "ram"
