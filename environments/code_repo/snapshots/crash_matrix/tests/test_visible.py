from crashsim import predict


def test_exact_block_multiple_no_crash():
    # length=1000, block=100 → 10 页；vram=1000 + 10*100*8 = 9000 < 100000
    r = predict(1000, 100, 8, card_vram_bytes=100000, base_bytes=1000, threshold=1.0)
    assert r["vram_bytes"] == 9000
    assert r["crash"] is False
