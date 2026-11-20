from sched import schedule


def test_single_request_prefill_on_faster():
    devs = [{"name": "A", "prefill_tps": 100, "decode_tps": 10},
            {"name": "B", "prefill_tps": 50, "decode_tps": 20}]
    res = schedule(devs, [{"id": "r1", "prompt_len": 1000, "completion_len": 200}])
    assert res["completion"]["r1"] == 20.0
    assert res["makespan"] == 20.0


def test_two_requests_balanced_prefill():
    devs = [{"name": "A", "prefill_tps": 100, "decode_tps": 10},
            {"name": "B", "prefill_tps": 100, "decode_tps": 20}]
    res = schedule(devs, [{"id": "r1", "prompt_len": 1000, "completion_len": 0},
                          {"id": "r2", "prompt_len": 1000, "completion_len": 0}])
    assert res["makespan"] == 10.0
    assert res["completion"]["r1"] == 10.0
    assert res["completion"]["r2"] == 10.0


def test_imbalanced_load_prefers_balanced_assignment():
    devs = [{"name": "A", "prefill_tps": 100, "decode_tps": 10},
            {"name": "B", "prefill_tps": 50, "decode_tps": 20}]
    res = schedule(devs, [{"id": "r1", "prompt_len": 1500, "completion_len": 0},
                          {"id": "r2", "prompt_len": 500, "completion_len": 0}])
    # r1→A(15s), r2→B(10s) -> prefill 15s；全 decode 0
    assert res["makespan"] == 15.0
    assert res["completion"]["r1"] == 15.0
    assert res["completion"]["r2"] == 15.0


def test_no_prompt_all_decode_on_faster_decode_card():
    devs = [{"name": "A", "prefill_tps": 100, "decode_tps": 10},
            {"name": "B", "prefill_tps": 50, "decode_tps": 30}]
    res = schedule(devs, [{"id": "r1", "prompt_len": 0, "completion_len": 100},
                          {"id": "r2", "prompt_len": 0, "completion_len": 300}])
    # prefill 0；decode 全在 B(30 tps) 顺序：r1=100/30, r2=400/30
    assert res["completion"]["r1"] == 100 / 30
    assert res["completion"]["r2"] == 400 / 30
    assert res["makespan"] == 400 / 30


def test_no_decode_all_prefill_respects_no_decode_during_prefill():
    # 两个请求都只有 prompt；makespan 应是平衡后的 prefill 时间，不含任何 decode 重叠
    devs = [{"name": "A", "prefill_tps": 200, "decode_tps": 5},
            {"name": "B", "prefill_tps": 200, "decode_tps": 5}]
    res = schedule(devs, [{"id": "r1", "prompt_len": 1000, "completion_len": 0},
                          {"id": "r2", "prompt_len": 1000, "completion_len": 0}])
    assert res["makespan"] == 5.0
