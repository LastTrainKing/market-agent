# -*- coding: utf-8 -*-
"""builtin.py —— 三个内置 Agent

不是越多越好。三个覆盖了"复盘—解读—研究"这条主线，
再加一个用户自建的入口，就够了。
"""

from __future__ import annotations

import os

from .agent import AgentSpec, AgentRegistry


REVIEW_PERSONA = """你是一位只做复盘、不做预测的盘面分析师。

你的工作方式：
1. 先看本地有哪些数据（list_panels），没有就直说没有。
2. 有数据就跑辨识度（run_salience），看当天资金真正在哪些板块聚集。
3. 用事实说话：给出板块名、净额、活跃度，不给"可能""或许"这类模糊词。
4. 明确区分"数据说了什么"与"我怎么看"。前者必须有工具结果支撑。

你**不做**的事：不预测明天涨跌，不给买卖建议，不编造没有的数据。"""

INTERPRET_PERSONA = """你是一位盘面解读员，只解释已经存在的数据。

你的工作方式：
1. 只读本地面板（read_panel / query_history），不引入任何外部数据。
2. 把数字翻译成"发生了什么"：哪个板块在吸金、哪个在失血、变化有多快。
3. 用户问"为什么"时，如果数据里没有答案，就回答"数据不支持这个结论"。

你不猜、不推断动机、不讲故事。数据能支撑到哪，你就说到哪。"""

RESEARCH_PERSONA = """你是一位研究助理，帮用户整理与对照研究材料。

你的工作方式：
1. 用户给的资料、笔记、研报，先读再答。
2. 做摘要时保留数字与出处；无法核实的标注"未核实"。
3. 对照多个来源时，先列一致点，再列分歧点，不要和稀泥。

你**不做**的事：不给目标价，不预测业绩，不把推测写成事实。"""


def build_builtin_registry(path: str = "agents.json",
                           workspace: str = ".") -> AgentRegistry:
    """创建/加载 Agent 注册表，并确保三个内置 Agent 存在。"""
    reg = AgentRegistry(path)

    builtins = [
        AgentSpec(
            id="reviewer",
            name="复盘助手",
            persona=REVIEW_PERSONA,
            provider="offline",
            tools=["_builtin_list_panels", "_builtin_salience",
                   "_builtin_report", "read_panel", "run_salience",
                   "query_history", "write_report"],
            level="write",
            max_steps=10,
            workspace=workspace,
            builtin=True,
        ),
        AgentSpec(
            id="interpreter",
            name="盘面解读",
            persona=INTERPRET_PERSONA,
            provider="offline",
            tools=["_builtin_list_panels", "read_panel", "query_history",
                   "run_salience"],
            level="read",
            max_steps=8,
            workspace=workspace,
            builtin=True,
        ),
        AgentSpec(
            id="researcher",
            name="研究助理",
            persona=RESEARCH_PERSONA,
            provider="offline",
            tools=["_builtin_list_panels", "query_history"],
            level="read",
            max_steps=8,
            workspace=workspace,
            builtin=True,
        ),
    ]

    changed = False
    for spec in builtins:
        cur = reg.get(spec.id)
        if cur is None:
            reg.add(spec)
            changed = True
    if changed:
        reg.save()
    return reg


def new_custom_agent(reg: AgentRegistry, agent_id: str, name: str,
                     persona: str, *, level: str = "read",
                     tools: list | None = None,
                     provider: str = "offline",
                     workspace: str = ".") -> AgentSpec:
    """用户自建 Agent 的入口。"""
    if reg.get(agent_id) is not None:
        raise ValueError("Agent id 已存在：%s" % agent_id)
    if level not in ("read", "write"):
        raise ValueError("level 只能是 read 或 write")
    spec = AgentSpec(
        id=agent_id, name=name, persona=persona, provider=provider,
        tools=tools or [], level=level, workspace=workspace, builtin=False,
    )
    reg.add(spec)
    return spec
