# 回复截断投递器 · 规格（实现 proxy/deliver.py）

`deliver(reply, kind, hard_limit) -> dict`
- `reply`：完整回复文本。
- `kind`：`foreground` / `background` / `streaming`。
- `hard_limit`：字节上限，可缺省（None）。
返回 `{"delivered": str, "truncated": bool, "reason": str | None}`。

规则（都有坑，别想当然）：
- foreground：永不截断，truncated=False，reason=None。
- background：仅当 hard_limit 非 None 时才截到 hard_limit 字节；hard_limit 为 None 时
  **不截**（后台默认无上限）。截断时 truncated=True，reason="hard-limit"。
- streaming：即使 hard_limit 非 None 也**不截**（流式必须完整交付），truncated=False，
  reason=None。
- 截断按字节切，不得把多字节 UTF-8 字符拦腰切断（切到不完整字符则回退到上一个完整字符）。
