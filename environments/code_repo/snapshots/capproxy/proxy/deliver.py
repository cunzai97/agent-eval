"""回复截断投递器。deliver 契约见 spec.md。"""


def deliver(reply, kind, hard_limit=None):
    """返回 {"delivered": str, "truncated": bool, "reason": str|None}。"""
    if hard_limit is not None:
        reply = reply.encode("utf-8")[:hard_limit].decode("utf-8")
    return {"delivered": reply, "truncated": False, "reason": None}
