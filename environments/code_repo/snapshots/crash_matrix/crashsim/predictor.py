"""VRAM 崩溃预测器。实现 predict，契约见 spec.md。"""


def predict(length, block, quant_bits, card_vram_bytes, base_bytes, threshold):
    """返回 {"vram_bytes": int, "crash": bool}。"""
    # TODO: 页数向上取整；vram=base+页数*block*quant_bits；crash 阈值判定
    raise NotImplementedError
