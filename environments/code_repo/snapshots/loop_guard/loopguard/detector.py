"""工具循环检测器。实现 LoopDetector.feed，契约见仓库根 spec.md。"""
from collections import deque


class LoopDetector:
    def __init__(self):
        # TODO: 维护事件窗口、连续计数、升级记忆
        pass

    def feed(self, tool: str, h: str) -> str:
        """追加一个事件，返回 ok/warn/block/pause 之一。"""
        # TODO: 实现三个信号 + 升级记忆 + 误报边界
        raise NotImplementedError
