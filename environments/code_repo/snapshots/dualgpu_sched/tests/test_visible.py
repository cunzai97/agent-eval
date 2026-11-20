from sched import schedule


def test_single_request_prefill_on_faster():
    devs = [{"name": "A", "prefill_tps": 100, "decode_tps": 10},
            {"name": "B", "prefill_tps": 50, "decode_tps": 20}]
    res = schedule(devs, [{"id": "r1", "prompt_len": 1000, "completion_len": 200}])
    # prefill 指给 A(10s)，decode 全在 B(200/20=10s)，完成=20
    assert res["completion"]["r1"] == 20.0
    assert res["makespan"] == 20.0
