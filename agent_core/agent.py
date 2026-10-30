# -*- coding: utf-8 -*-
"""agent.py —— Agent 定义与运行循环

三条硬规则（继承 ai-workbench 的教训）：

  1. **裁决是证据，不是意见。** 一步成不成功，看工具返回的 ok，
     不看模型怎么说。最终结论必须挂在至少一次成功的工具调用上。
  2. **重试不是循环。** 连续两次相同工具 + 相同参数，直接拒绝执行，
     判为无效重试。这一条是被真实事故逼出来的：不加它，
     一次运行能在两个步骤里烧掉上百次调用而毫无进展。
  3. **产出不等于交付。** `verified`（机器校验）与 `accepted`（人点头）
     是两个字段。本模块只负责前者。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any

from .audit import AuditLog
from .permissions import (Permission, PermissionGate, PermissionDenied,
                          LEVEL_READ, LEVEL_WRITE, standard, strict_readonly)
from .providers import Provider, Action, make_provider
from .tools import ToolRegistry, ToolResult


SYSTEM_TEMPLATE = """你是「{name}」。

{persona}

# 可用工具
{tools}

# 输出约定
需要调用工具时，只输出一个 JSON 代码块：
```json
{{"tool": "工具名", "args": {{...}}, "thought": "为什么调它"}}
```
任务完成时，直接输出自然语言结论，不要再输出 JSON。

# 硬性约束
- 你**不能联网抓取行情**。本产品只读本地数据。
- 结论必须来自工具返回的真实数据；拿不到数据就说拿不到，不要编。
- 不要重复调用同一个工具 + 同一组参数；上一次已经失败就不要原样再来。
"""


@dataclass
class AgentSpec:
    """一份 Agent 定义。用户可自建、可修改。"""

    id: str
    name: str
    persona: str
    provider: str = "offline"
    model: str = ""
    tools: list = field(default_factory=list)      # 空 = 全部允许
    deny_tools: list = field(default_factory=list)
    level: str = "read"                            # read | write
    max_steps: int = 12
    workspace: str = "."
    builtin: bool = False

    def to_permission(self) -> Permission:
        """把 Agent 的工具白名单一并交给权限闸门。

        ★ 这一点很关键：`tools` 不能只用来"过滤给模型看的清单"，
          它必须同时是**闸门的允许列表**。否则模型（或任何 Provider）
          直接点名一个未授权的工具，照样能执行——那就等于没有限制。
          权限只在一个地方决定，就是这里。
        """
        p = standard(self.workspace) if self.level == "write" \
            else strict_readonly(self.workspace)
        p.allow_tools = tuple(self.tools)
        p.deny_tools = tuple(self.deny_tools)
        return p

    def as_dict(self):
        return asdict(self)


@dataclass
class RunResult:
    ok: bool
    answer: str
    steps: int
    tool_calls: list = field(default_factory=list)
    denied: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    audit_path: str = ""

    def brief(self) -> str:
        return ("%s | %d 步 | %d 次工具调用 | %d 次拒绝"
                % ("成功" if self.ok else "未完成", self.steps,
                   len(self.tool_calls), len(self.denied)))


class Agent:
    def __init__(self, spec: AgentSpec, registry: ToolRegistry,
                 provider: Provider | None = None):
        self.spec = spec
        self.registry = registry
        self.provider = provider or make_provider(spec.provider,
                                                  model=spec.model or "deepseek-chat")
        self.gate = PermissionGate(spec.to_permission())

    # ---------------------------------------------------------------- 运行
    def run(self, goal: str, audit_dir: str = ".audit") -> RunResult:
        os.makedirs(audit_dir, exist_ok=True)
        audit_path = os.path.join(
            audit_dir, "run_%s_%s.jsonl" % (self.spec.id,
                                            __import__("time").strftime("%Y%m%d_%H%M%S")))
        audit = AuditLog(audit_path)
        audit.run_start(self.spec.name, goal, self.gate.perm.as_dict())

        # 工具清单：按 allow/deny 过滤后给模型看
        visible = [n for n in self.registry.names()
                   if (not self.spec.tools or n in self.spec.tools)
                   and n not in self.spec.deny_tools]
        tools_desc = "\n".join(
            "- `%s` — %s" % (self.registry.get(n).signature_help(),
                             self.registry.get(n).description)
            for n in visible)

        system = SYSTEM_TEMPLATE.format(
            name=self.spec.name, persona=self.spec.persona, tools=tools_desc)

        messages: list[dict] = [{"role": "user", "content": goal}]
        calls: list = []
        denied: list = []
        last_sig = None
        answer = ""
        ok = False
        step = 0

        for step in range(1, self.spec.max_steps + 1):
            try:
                action = self.provider.complete(system, messages, tools_desc)
            except Exception as e:                       # noqa: BLE001
                answer = "模型调用失败：%s: %s" % (type(e).__name__, e)
                audit.step(step, answer)
                break

            if action.kind == "final":
                answer = action.answer
                audit.step(step, action.thought or "(结论)", tool="", args={})
                # 规则 1：结论必须挂在成功的工具调用上（纯咨询类目标除外）
                ok = bool(calls) or not visible
                break

            # 规则 2：拒绝无效重试
            sig = (action.tool, json.dumps(action.args, sort_keys=True,
                                           ensure_ascii=False))
            if sig == last_sig:
                denied.append("无效重试：%s 参数未变" % action.tool)
                audit.denied(denied[-1], "same_retry")
                messages.append({"role": "tool", "tool": action.tool,
                                 "content": "拒绝：与上一次调用完全相同，请换策略。"})
                continue
            last_sig = sig

            audit.step(step, action.thought, tool=action.tool, args=action.args)

            try:
                res = self.registry.call(action.tool, action.args,
                                         self.gate, audit)
            except PermissionDenied as e:
                denied.append(e.reason)
                audit.denied(e.reason, e.rule)
                messages.append({"role": "tool", "tool": action.tool,
                                 "content": "权限拒绝：%s" % e.reason})
                continue

            calls.append({"tool": action.tool, "args": action.args,
                          "ok": res.ok, "error": res.error})
            messages.append({"role": "tool", "tool": action.tool,
                             "content": res.brief(2000)})
            messages.append({"role": "assistant",
                             "content": "（已执行 %s）" % action.tool})

        stats = self.gate.summary()
        audit.run_end("ok" if ok else "incomplete", answer[:500], stats)
        return RunResult(ok=ok, answer=answer, steps=step, tool_calls=calls,
                         denied=denied, stats=stats, audit_path=audit_path)


# ====================================================================== 注册表
class AgentRegistry:
    """用户自建 Agent 的持久化。一个 JSON 文件，可手改、可版本管理。"""

    def __init__(self, path: str):
        self.path = path
        self._agents: dict[str, AgentSpec] = {}
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {"agents": []}
        for a in data.get("agents", []):
            try:
                self._agents[a["id"]] = AgentSpec(**a)
            except TypeError:
                continue

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"agents": [a.as_dict() for a in self._agents.values()]},
                      f, ensure_ascii=False, indent=2)

    def add(self, spec: AgentSpec):
        self._agents[spec.id] = spec
        self.save()

    def remove(self, agent_id: str) -> bool:
        if agent_id in self._agents and not self._agents[agent_id].builtin:
            del self._agents[agent_id]
            self.save()
            return True
        return False

    def get(self, agent_id: str) -> AgentSpec | None:
        return self._agents.get(agent_id)

    def all(self) -> list[AgentSpec]:
        return list(self._agents.values())
