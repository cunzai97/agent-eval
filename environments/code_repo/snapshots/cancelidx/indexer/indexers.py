"""语义索引器。修复 Indexer，契约见 spec.md。"""


class Indexer:
    def __init__(self):
        self.cancelled = False
        self.output = []      # 已索引完成的 doc 标记
        self.pending = []     # 内部任务队列
        self.dirty = False    # 脏标志（取消后应清零以可复用）

    def cancel(self):
        self.cancelled = True

    def run(self, docs, on_iter=None):
        # bug: 取消位只在入口检查一次
        if self.cancelled:
            return self.output
        for i, d in enumerate(docs):
            if on_iter:
                on_iter(i)
            self.output.append(d)      # 无条件写满
            self.pending.append(d)     # 任务堆积不排空
        self.dirty = True              # 标记脏
        return self.output
