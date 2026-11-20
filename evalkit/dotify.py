"""把 dict 转成支持点访问的对象，并提供受限安全的表达式求值。

断言表达式是 Python 语法，可用变量：
  state : 环境终态（dict，支持 state.a.b 或 state["a"] 或 'x' in state.list）
  traj  : 轨迹摘要（dict）
例：
  state.test_report.failed == 0
  'calc.py' in state.files_changed
  len(state.out.result.rows) == 12
"""
from __future__ import annotations
from typing import Any


class DotDict:
    __slots__ = ("_d",)

    def __init__(self, d: dict):
        object.__setattr__(self, "_d", {str(k): _wrap(v) for k, v in d.items()})

    def __getattr__(self, k):
        try:
            return self._d[k]
        except KeyError:
            raise AttributeError(k) from None

    def __getitem__(self, k):
        return self._d[k]

    def __contains__(self, k):
        return k in self._d

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def get(self, k, default=None):
        return self._d.get(k, default)

    def __repr__(self):
        return repr(self._d)

    def __eq__(self, other):
        """Keep wrapped state comparable with literal dicts in verifier expressions."""
        if isinstance(other, DotDict):
            return self._d == other._d
        if isinstance(other, dict):
            return self._d == other
        return NotImplemented

def _wrap(v):
    if isinstance(v, dict):
        return DotDict(v)
    if isinstance(v, list):
        return [_wrap(x) for x in v]
    return v


def dotify(v):
    return _wrap(v)


# 受限 builtins：只允许纯计算函数，禁止 import/exec/open 等
_SAFE = {
    "len": len, "sum": sum, "any": any, "all": all,
    "min": min, "max": max, "abs": abs, "round": round,
    "int": int, "float": float, "str": str, "bool": bool,
    "list": list, "dict": dict, "set": set, "sorted": sorted,
    "True": True, "False": False, "None": None,
}


def eval_expr(expr: str, state: dict, traj: dict | None) -> Any:
    g = {"__builtins__": {}}
    g.update(_SAFE)
    g["state"] = dotify(state or {})
    g["traj"] = dotify(traj or {})
    return eval(expr, g, {})  # noqa: S307 - state 为受控数据，builtins 已禁用
