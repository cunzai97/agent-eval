from crashsim import predict


def test_ceil_last_partial_block():
    # length=1050, block=100 → ceil=11 页（最后一块 50 也按整块）
    r = predict(1050, 100, 8, card_vram_bytes=100000, base_bytes=1000, threshold=1.0)
    assert r["vram_bytes"] == 1000 + 11 * 100 * 8  # 9800
    assert r["crash"] is False


def test_threshold_boundary_crash():
    # 恰好达到阈值 → crash
    # vram = 2000 + 10*100*8 = 10000 ; card=10000 ; θ=1.0 → ratio=1.0 → crash
    r = predict(1000, 100, 8, card_vram_bytes=10000, base_bytes=2000, threshold=1.0)
    assert r["vram_bytes"] == 10000
    assert r["crash"] is True


def test_below_threshold_safe():
    # ratio 略低于阈值 → safe
    r = predict(1000, 100, 8, card_vram_bytes=10001, base_bytes=2000, threshold=1.0)
    assert r["crash"] is False


def test_low_bits_reduces_vram():
    # 4-bit 比 8-bit 显存减半（KV 部分）
    hi = predict(2000, 100, 8, card_vram_bytes=10**9, base_bytes=0, threshold=1.0)
    lo = predict(2000, 100, 4, card_vram_bytes=10**9, base_bytes=0, threshold=1.0)
    assert hi["vram_bytes"] == 20 * 100 * 8
    assert lo["vram_bytes"] == 20 * 100 * 4
    assert lo["vram_bytes"] == hi["vram_bytes"] // 2


def test_zero_length_only_base():
    r = predict(0, 100, 8, card_vram_bytes=1000, base_bytes=100, threshold=1.0)
    assert r["vram_bytes"] == 100
