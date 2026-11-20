from compress import build_cli, run_pipeline


def test_cli_parses_threshold():
    cfg = build_cli(["--threshold", "0.8"])
    assert cfg["threshold"] == 0.8
