# -*- coding: utf-8 -*-
"""tools.py —— 工具注册与执行

原则：
  * 工具是**唯一**能产生副作用的东西
  * 执行必须过 `PermissionGate`，没有旁路
  * 每次调用都写审计
  * 工具返回结构化结果，不返回裸字符串（便于机器校验）
"""

from __future__ import annotations

import inspect
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .permissions import PermissionGate, PermissionDenied


class ToolError(Exception):
    """工具执行失败。与权限拒绝区分开：这是"做了但没做成"。"""


@dataclass
class ToolSpec:
    name: str
    description: str
    func: Callable
    required_level: int = 1
    params: dict = field(default_factory=dict)     # 参数说明，给模型看
    returns: str = ""                              # 返回值说明

    def signature_help(self) -> str:
        if not self.params:
            return "%s()" % self.name
        parts = []
        for k, v in self.params.items():
            parts.append("%s: %s" % (k, v))
        return "%s(%s)" % (self.name, ", ".join(parts))


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    duration_ms: int = 0

    def brief(self, limit: int = 400) -> str:
        if not self.ok:
            return "[失败] " + self.error
        if isinstance(self.data, str):
            s = self.data
        else:
            s = repr(self.data)
        return s if len(s) <= limit else s[:limit] + "…"


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    # ---------------------------------------------------------------- 注册
    def register(self, name: str, description: str, required_level: int = 1,
                 params: dict | None = None, returns: str = ""):
        def deco(fn: Callable):
            sig = inspect.signature(fn)
            self._tools[name] = ToolSpec(
                name=name, description=description, func=fn,
                required_level=required_level,
                params=params or {p: "any" for p in sig.parameters},
                returns=returns,
            )
            return fn
        return deco

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> str:
        lines = []
        for n in self.names():
            t = self._tools[n]
            lines.append("- `%s` — %s" % (t.signature_help(), t.description))
        return "\n".join(lines)

    # ---------------------------------------------------------------- 执行
    def call(self, name: str, args: dict, gate: PermissionGate,
             audit=None) -> ToolResult:
        import time
        t0 = time.time()
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(False, error="未知工具：%s" % name)

        # 权限：不通过直接抛出，让上层记录并终止该步
        gate.check(name, spec.required_level, args)

        try:
            out = spec.func(**args)
            res = ToolResult(True, data=out)
        except PermissionDenied:
            raise
        except TypeError as e:
            res = ToolResult(False, error="参数不匹配：%s" % e)
        except Exception as e:                       # noqa: BLE001
            res = ToolResult(False, error="%s: %s" % (type(e).__name__, e),
                             data=traceback.format_exc(limit=2))

        res.duration_ms = int((time.time() - t0) * 1000)
        if spec.required_level >= 2 and res.ok:
            gate.note_write()
        elif res.ok:
            gate.note_read()

        if audit is not None:
            audit.tool_call(name, args, res)
        return res
