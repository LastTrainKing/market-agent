# -*- coding: utf-8 -*-
"""permissions.py —— 权限闸门

设计原则（继承 ai-workbench）：
  **一个地方决定 Agent 能碰什么**。所有工具调用必须过 `PermissionGate.check()`，
  没有旁路。

四层判定，全部通过才放行：
  1. 等级（level 0-3）
  2. 路径（workspace 沙箱）
  3. 网络（本产品硬编码为禁止）
  4. 工具白名单
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable


class PermissionDenied(Exception):
    """权限拒绝。带一句可直接展示给用户的原因。"""

    def __init__(self, reason: str, *, rule: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.rule = rule


LEVEL_NONE = 0        # 只读元信息
LEVEL_READ = 1        # 读本地数据
LEVEL_WRITE = 2       # 写工作区文件
LEVEL_EXEC = 3        # 执行命令（本产品默认不开放）

LEVEL_NAMES = {
    LEVEL_NONE: "none",
    LEVEL_READ: "read",
    LEVEL_WRITE: "write",
    LEVEL_EXEC: "exec",
}


@dataclass
class Permission:
    """一份权限配置。"""

    level: int = LEVEL_READ
    workspace: str = "."
    allow_tools: tuple = ()          # 空 = 不限制（仍受 level 约束）
    deny_tools: tuple = ()
    network: bool = False            # ★ 硬编码：本产品永远 False
    max_writes: int = 200            # 单次运行写文件上限

    def as_dict(self):
        return {
            "level": LEVEL_NAMES.get(self.level, str(self.level)),
            "workspace": self.workspace,
            "allow_tools": list(self.allow_tools),
            "deny_tools": list(self.deny_tools),
            "network": self.network,
            "max_writes": self.max_writes,
        }


@dataclass
class GateStats:
    writes: int = 0
    reads: int = 0
    denied: int = 0
    denied_reasons: list = field(default_factory=list)


class PermissionGate:
    """唯一的权限判定入口。"""

    # 工具声明的必需等级：工具在注册时给出 required_level
    def __init__(self, perm: Permission):
        if perm.network:
            raise ValueError(
                "本产品禁止 Agent 联网：network 必须为 False。"
                "理由：上游行情接口已对爬虫来源做风控标记，"
                "联网取数会导致运行机器 IP 被封禁。")
        self.perm = perm
        self.stats = GateStats()
        self._root = os.path.abspath(perm.workspace or ".")

    # ---------------------------------------------------------------- 判定
    def check(self, tool_name: str, required_level: int,
              args: dict | None = None) -> None:
        """不通过就抛 PermissionDenied。"""
        args = args or {}

        # 1) 工具白名单
        if tool_name in self.perm.deny_tools:
            self._deny("工具 %s 在禁止列表中" % tool_name, "deny_tools")
        if self.perm.allow_tools and tool_name not in self.perm.allow_tools:
            self._deny("工具 %s 不在允许列表中" % tool_name, "allow_tools")

        # 2) 等级
        if required_level > self.perm.level:
            self._deny(
                "工具 %s 需要 %s 级权限，当前为 %s 级"
                % (tool_name,
                   LEVEL_NAMES.get(required_level, required_level),
                   LEVEL_NAMES.get(self.perm.level, self.perm.level)),
                "level")

        # 3) 路径沙箱（任何带 path 的参数都要在 workspace 内）
        for key in ("path", "out", "dest", "src", "file"):
            if key in args and args[key]:
                self.resolve_path(str(args[key]))

        # 4) 写入配额
        if required_level >= LEVEL_WRITE and self.stats.writes >= self.perm.max_writes:
            self._deny("本次运行写入次数已达上限 %d" % self.perm.max_writes, "quota")

    def resolve_path(self, p: str) -> str:
        """把相对路径解析到 workspace 内，越界即拒绝。"""
        if os.path.isabs(p):
            full = os.path.abspath(p)
        else:
            full = os.path.abspath(os.path.join(self._root, p))
        if not (full == self._root or full.startswith(self._root + os.sep)):
            self._deny("路径越界：%s 不在工作区 %s 内" % (p, self._root), "sandbox")
        return full

    # ---------------------------------------------------------------- 记账
    def note_write(self):
        self.stats.writes += 1

    def note_read(self):
        self.stats.reads += 1

    def _deny(self, reason: str, rule: str):
        self.stats.denied += 1
        self.stats.denied_reasons.append(reason)
        raise PermissionDenied(reason, rule=rule)

    def summary(self) -> dict:
        return {
            "level": LEVEL_NAMES.get(self.perm.level, str(self.perm.level)),
            "workspace": self._root,
            "network": False,
            "writes": self.stats.writes,
            "reads": self.stats.reads,
            "denied": self.stats.denied,
            "denied_reasons": self.stats.denied_reasons[-5:],
        }


def strict_readonly(workspace: str = ".") -> Permission:
    """只读配置：只能读工作区内的数据，不能写、不能执行。"""
    return Permission(level=LEVEL_READ, workspace=workspace)


def standard(workspace: str = ".") -> Permission:
    """标准配置：可读 + 可写工作区（默认给内置 Agent 用）。"""
    return Permission(level=LEVEL_WRITE, workspace=workspace)
