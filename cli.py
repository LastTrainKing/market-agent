#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cli.py —— market-agent 命令行入口

用法：
    python cli.py list                          # 列出 Agent
    python cli.py tools                         # 列出工具
    python cli.py run reviewer "复盘最新一天"    # 跑内置 Agent
    python cli.py new my_agent "我的助手"       # 新建自定义 Agent
    python cli.py import data.csv 20261030      # 导入 CSV 面板
    python cli.py audit                         # 看最近审计
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_core.agent import Agent, AgentSpec
from agent_core.builtin import build_builtin_registry, new_custom_agent
from agent_core.permissions import PermissionDenied
from agent_core.providers import make_provider

import market_tools


def _reg():
    return build_builtin_registry(path="agents.json", workspace=".")


def cmd_list(argv):
    reg = _reg()
    print("%-14s %-12s %-8s %-6s %s" % ("ID", "名称", "等级", "步数", "类型"))
    print("-" * 60)
    for a in reg.all():
        print("%-14s %-12s %-8s %-6d %s"
              % (a.id, a.name, a.level, a.max_steps,
                 "内置" if a.builtin else "自定义"))
    return 0


def cmd_tools(argv):
    print("可用工具：\n")
    print(market_tools.registry.describe())
    return 0


def cmd_run(argv):
    if len(argv) < 2:
        print("用法：python cli.py run <agent_id> \"<目标>\"")
        return 2
    agent_id, goal = argv[0], argv[1]
    reg = _reg()
    spec = reg.get(agent_id)
    if spec is None:
        print("找不到 Agent：%s（用 list 查看）" % agent_id)
        return 2

    provider_name = os.environ.get("AGENT_PROVIDER", spec.provider)
    try:
        provider = make_provider(provider_name, model=spec.model or "deepseek-chat")
    except Exception as e:                       # noqa: BLE001
        print("Provider 初始化失败：%s" % e)
        return 2

    print("Agent : %s（%s）" % (spec.name, spec.id))
    print("模式 : %s%s" % (provider_name,
                           "" if provider_name == "offline" else "（需 AGENT_API_KEY）"))
    print("目标 : %s" % goal)
    print("-" * 60)

    agent = Agent(spec, market_tools.registry, provider=provider)
    try:
        res = agent.run(goal, audit_dir=".audit")
    except PermissionDenied as e:
        print("权限拒绝：%s" % e.reason)
        return 1

    print("\n" + "=" * 60)
    print("结论：\n")
    print(res.answer or "（空）")
    print("\n" + "=" * 60)
    print(res.brief())
    if res.denied:
        print("拒绝记录：")
        for d in res.denied:
            print("  - " + d)
    print("审计：%s" % res.audit_path)
    return 0 if res.ok else 1


def cmd_new(argv):
    if len(argv) < 2:
        print("用法：python cli.py new <id> <名称> [--level read|write] [--persona 文本]")
        return 2
    agent_id, name = argv[0], argv[1]
    level = "read"
    persona = "你是一位自定义分析助手。只依据本地数据作答，不编造。"
    i = 2
    while i < len(argv):
        if argv[i] == "--level" and i + 1 < len(argv):
            level = argv[i + 1]; i += 2
        elif argv[i] == "--persona" and i + 1 < len(argv):
            persona = argv[i + 1]; i += 2
        else:
            i += 1
    reg = _reg()
    try:
        spec = new_custom_agent(reg, agent_id, name, persona, level=level)
    except ValueError as e:
        print("创建失败：%s" % e)
        return 1
    print("已创建：%s（%s，等级 %s）" % (spec.name, spec.id, spec.level))
    print("配置写入 agents.json，可手改。")
    return 0


def cmd_import(argv):
    if len(argv) < 2:
        print("用法：python cli.py import <文件.csv> <tag>")
        return 2
    path, tag = argv[0], argv[1]
    from localio import import_csv
    try:
        meta, mat = import_csv(path, tag)
    except Exception as e:                       # noqa: BLE001
        print("导入失败：%s" % e)
        return 1
    print("导入成功：%s  %d 分钟 × %d 板块  形状 %s"
          % (meta["data_date"], len(meta["times"]), len(meta["codes"]), mat.shape))
    return 0


def cmd_audit(argv):
    from agent_core.audit import AuditLog
    d = ".audit"
    if not os.path.isdir(d):
        print("暂无审计记录")
        return 0
    files = sorted(os.listdir(d))
    if not files:
        print("暂无审计记录")
        return 0
    log = AuditLog(os.path.join(d, files[-1]))
    print("最近一次运行：%s\n" % files[-1])
    for rec in log.tail(15):
        kind = rec.get("kind")
        if kind == "tool_call":
            print("  [工具] %-20s ok=%s %dms" % (rec.get("tool"),
                                                 rec.get("ok"), rec.get("duration_ms", 0)))
        elif kind == "denied":
            print("  [拒绝] %s" % rec.get("reason"))
        elif kind == "step":
            print("  [步骤] %d  %s" % (rec.get("n"), (rec.get("thought") or "")[:60]))
        elif kind == "run_end":
            print("  [结束] %s  %s" % (rec.get("status"), rec.get("summary", "")[:60]))
    return 0


COMMANDS = {
    "list": cmd_list, "tools": cmd_tools, "run": cmd_run,
    "new": cmd_new, "import": cmd_import, "audit": cmd_audit,
}


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    fn = COMMANDS.get(argv[0])
    if fn is None:
        print("未知命令：%s\n" % argv[0])
        print(__doc__)
        return 2
    return fn(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
