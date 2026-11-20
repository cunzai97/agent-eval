"""双卡推理流水线调度。实现 schedule，契约见仓库根 spec.md。"""


def schedule(devices, requests):
    """返回 {"makespan": float, "completion": {id: float}}。"""
    # TODO: 阶段一 prefill 负载均衡指派；阶段二单卡顺序 decode
    raise NotImplementedError
