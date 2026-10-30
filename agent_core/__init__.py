# -*- coding: utf-8 -*-
"""market-agent —— A 股离线复盘 Agent 工作台

设计边界（先读这段）：
  * **工具集不联网。** 行情数据只来自本地导入，避免触发上游风控。
  * **模型可联网，但 Key 由用户自填。** 这是两件事，代码里分开。
  * **内置三个 Agent，用户可自建。** 定义存在一个 JSON 里，可手改、可版本管理。
"""

from .agent import Agent, AgentSpec, AgentRegistry, RunResult
from .permissions import (Permission, PermissionGate, PermissionDenied,
                          LEVEL_NONE, LEVEL_READ, LEVEL_WRITE, LEVEL_EXEC,
                          standard, strict_readonly)
from .providers import (Provider, OfflineProvider, OpenAICompatProvider,
                        Action, make_provider, parse_action)
from .tools import ToolRegistry, ToolSpec, ToolResult, ToolError
from .audit import AuditLog

__version__ = "0.1.0"

__all__ = [
    "Agent", "AgentSpec", "AgentRegistry", "RunResult",
    "Permission", "PermissionGate", "PermissionDenied",
    "LEVEL_NONE", "LEVEL_READ", "LEVEL_WRITE", "LEVEL_EXEC",
    "standard", "strict_readonly",
    "Provider", "OfflineProvider", "OpenAICompatProvider",
    "Action", "make_provider", "parse_action",
    "ToolRegistry", "ToolSpec", "ToolResult", "ToolError",
    "AuditLog",
]
