from compress import run_pipeline


def _run(t):
    return run_pipeline(["--threshold", str(t)])


def test_thresholds_strictly_decrease():
    o2, o5, o8 = _run(0.2), _run(0.5), _run(0.8)
    assert len(o2) > len(o5) > len(o8)
    assert o2 != o5 != o8


def test_all_three_outputs_distinct():
    outs = [_run(0.2), _run(0.5), _run(0.8)]
    assert len({tuple(o) for o in outs}) == 3


def test_high_threshold_keeps_fewest():
    o2, o8 = _run(0.2), _run(0.8)
    assert len(o8) < len(o2)
