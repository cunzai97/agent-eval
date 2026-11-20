"""混合精度量化策略。实现 classify，契约见 spec.md。"""


def classify(graph, sensitive, quantizable, byte_cost):
    """两遍：逐 tensor 意向精度 → 组一致性取最保守。返回 tiers/bytes_after/ratio。"""
    # TODO: 第一遍（sensitive 取规则精度 / quantizable 降 int8 / 未知保 fp16）；
    #      第二遍同组取最保守；核算字节
    raise NotImplementedError
