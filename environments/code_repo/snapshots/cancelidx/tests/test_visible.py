from indexer import Indexer


def test_run_without_cancel_completes():
    ix = Indexer()
    out = ix.run(["a", "b", "c"])
    assert out == ["a", "b", "c"]
