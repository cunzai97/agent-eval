from indexer import Indexer


def test_cancel_mid_run_stops_by_iteration():
    # on_iter(3) 注入 cancel → 第 4 个 doc 前停，只索引前 3 个
    ix = Indexer()
    def on_iter(i):
        if i == 3:
            ix.cancel()
    out = ix.run([f"d{i}" for i in range(10)], on_iter=on_iter)
    assert out == ["d0", "d1", "d2"]


def test_cancel_drains_pending_and_clears_dirty():
    ix = Indexer()
    ix.cancel()
    ix.run(["a", "b"], on_iter=lambda i: None)
    assert ix.pending == []     # 队列排空
    assert ix.dirty is False    # 脏标志清零


def test_cancelled_instance_reusable():
    ix = Indexer()
    def on_iter(i):
        if i == 1:
            ix.cancel()
    ix.run([f"d{i}" for i in range(5)], on_iter=on_iter)
    # 取消后实例可复用：再次 run 小列表能正常完成
    out2 = ix.run(["x", "y"])
    assert out2 == ["d0", "x", "y"]


def test_cancel_at_entry_stops_immediately():
    ix = Indexer()
    ix.cancel()
    out = ix.run(["a", "b", "c"])
    assert out == []
    assert ix.pending == []
